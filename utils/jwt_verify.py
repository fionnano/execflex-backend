"""
Verify Supabase Auth access tokens.

This service does not issue login tokens. The browser (execo-bridge) and the
Ainm backend obtain a user access token from Supabase Auth (GoTrue) and send
it as ``Authorization: Bearer``. Those tokens are JWTs:

- Legacy projects sign them with HS256 and the project JWT secret.
- Projects on Supabase asymmetric signing keys use ES256 or RS256. The public
  keys are published at ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``.

A token is accepted only when the signature checks out against that key, the
``alg`` header is on the fixed allow-list, and ``exp``, ``aud``, ``iss``,
``sub`` and ``role`` match what Supabase puts on a user access token. The
``none`` algorithm is rejected. If the key material for the token's algorithm
is not configured, verification fails closed.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

import jwt

MIN_JWT_SECRET_LENGTH = 32
JWT_LEEWAY_SECONDS = 30
ALLOWED_HMAC_ALGS = frozenset({"HS256"})
ALLOWED_ASYMMETRIC_ALGS = frozenset({"ES256", "RS256"})
ALLOWED_ALGS = ALLOWED_HMAC_ALGS | ALLOWED_ASYMMETRIC_ALGS
USER_ROLE = "authenticated"
APP_ROLES = frozenset({"owner", "recruiter", "viewer"})

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_jwk_client = None
_jwk_client_url: Optional[str] = None
_warned_config = False


class TokenVerificationError(Exception):
    """A bearer token must not be treated as authenticated."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def is_uuid(value: Any) -> bool:
    return bool(value) and bool(_UUID_RE.match(str(value)))


def _raw_jwt_secret() -> str:
    """The configured HMAC secret, including values that are too short to use."""
    secret = (os.getenv("SUPABASE_JWT_SECRET") or "").strip()
    if secret:
        return secret
    try:
        from config.app_config import SUPABASE_JWT_SECRET
        return (SUPABASE_JWT_SECRET or "").strip()
    except Exception:
        return ""


def jwt_hmac_secret() -> Optional[str]:
    """
    HMAC secret for HS256 Supabase access tokens.

    Returns None when the secret is missing or shorter than 32 characters.
    A short value is ignored so a placeholder cannot verify forged tokens.
    """
    secret = _raw_jwt_secret()
    if len(secret) < MIN_JWT_SECRET_LENGTH:
        return None
    return secret


def expected_audience() -> str:
    audience = (os.getenv("SUPABASE_JWT_AUD") or "authenticated").strip()
    return audience or "authenticated"


def expected_issuer() -> Optional[str]:
    """
    Issuer claim Supabase writes on user access tokens.

    Override with SUPABASE_JWT_ISS when the project uses a custom issuer.
    Otherwise the issuer is ``{SUPABASE_URL}/auth/v1``.
    """
    override = (os.getenv("SUPABASE_JWT_ISS") or "").strip().rstrip("/")
    if override:
        return override
    url = (os.getenv("SUPABASE_URL") or "").strip()
    if not url:
        try:
            from config.app_config import SUPABASE_URL
            url = (SUPABASE_URL or "").strip()
        except Exception:
            url = ""
    url = url.rstrip("/")
    if not url:
        return None
    return f"{url}/auth/v1"


def jwks_url() -> Optional[str]:
    override = (os.getenv("SUPABASE_JWKS_URL") or "").strip()
    if override:
        return override
    base = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    if not base:
        try:
            from config.app_config import SUPABASE_URL
            base = (SUPABASE_URL or "").strip().rstrip("/")
        except Exception:
            base = ""
    if not base:
        return None
    return f"{base}/auth/v1/.well-known/jwks.json"


def jwks_explicitly_enabled() -> bool:
    """
    Production may boot without SUPABASE_JWT_SECRET only when this is set.

    Asymmetric tokens are still verified whenever a JWKS URL can be built.
    The flag is the explicit acknowledgement that HS256 is not in use.
    """
    return (os.getenv("SUPABASE_JWT_JWKS") or "").strip().lower() in ("1", "true", "yes")


def auth_configuration_errors() -> list[str]:
    """Problems that make user-token verification impossible. Safe to log."""
    errors = []
    if not expected_issuer():
        errors.append(
            "JWT issuer is not configured (set SUPABASE_URL or SUPABASE_JWT_ISS)"
        )
    raw = _raw_jwt_secret()
    if raw and len(raw) < MIN_JWT_SECRET_LENGTH:
        errors.append(
            "SUPABASE_JWT_SECRET is shorter than "
            f"{MIN_JWT_SECRET_LENGTH} characters and will be ignored"
        )
    if not jwt_hmac_secret() and not jwks_explicitly_enabled():
        errors.append(
            "No JWT verification key is configured. Set SUPABASE_JWT_SECRET "
            "(Supabase Dashboard → Settings → API → JWT Secret) or "
            "SUPABASE_JWT_JWKS=1 if this project uses asymmetric signing keys only"
        )
    return errors


def warn_if_unconfigured() -> None:
    global _warned_config
    errors = auth_configuration_errors()
    if not errors or _warned_config:
        return
    _warned_config = True
    for error in errors:
        print(f"SECURITY: {error}. User JWTs will be rejected.", flush=True)


def _header_alg(token: str) -> str:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise TokenVerificationError("Invalid token format") from exc
    alg = header.get("alg") if isinstance(header, dict) else None
    if not isinstance(alg, str) or alg.lower() == "none" or alg not in ALLOWED_ALGS:
        raise TokenVerificationError("Invalid token algorithm")
    return alg


def _asymmetric_signing_key(token: str):
    """Public key from the Supabase JWKS for this token's ``kid``."""
    global _jwk_client, _jwk_client_url
    url = jwks_url()
    if not url:
        raise TokenVerificationError("Token verification is not configured")
    if _jwk_client is None or _jwk_client_url != url:
        _jwk_client = jwt.PyJWKClient(url, cache_keys=True, timeout=5)
        _jwk_client_url = url
    try:
        return _jwk_client.get_signing_key_from_jwt(token).key
    except TokenVerificationError:
        raise
    except Exception as exc:
        raise TokenVerificationError("Invalid token signature") from exc


def verify_supabase_access_token(token: str) -> dict:
    """
    Return the verified payload or raise TokenVerificationError.

    The header algorithm selects the key, and that algorithm is the only one
    passed to the decoder. HS256 uses the JWT secret; ES256/RS256 use JWKS.
    The public key is never used as an HMAC secret.
    """
    if not token or not isinstance(token, str):
        raise TokenVerificationError("Missing token")

    alg = _header_alg(token)
    issuer = expected_issuer()
    audience = expected_audience()
    if not issuer or not audience:
        raise TokenVerificationError("Token verification is not configured")

    if alg in ALLOWED_HMAC_ALGS:
        secret = jwt_hmac_secret()
        if not secret:
            warn_if_unconfigured()
            raise TokenVerificationError("Token verification is not configured")
        key = secret
    else:
        key = _asymmetric_signing_key(token)

    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=audience,
            issuer=issuer,
            leeway=JWT_LEEWAY_SECONDS,
            options={
                "require": ["exp", "sub", "aud", "iss"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenVerificationError("Token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenVerificationError("Invalid token audience") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenVerificationError("Invalid token issuer") from exc
    except jwt.InvalidSignatureError as exc:
        raise TokenVerificationError("Invalid token signature") from exc
    except jwt.InvalidKeyError as exc:
        raise TokenVerificationError("Invalid token signature") from exc
    except jwt.InvalidAlgorithmError as exc:
        raise TokenVerificationError("Invalid token algorithm") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise TokenVerificationError("Token is missing required claims") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenVerificationError("Invalid token") from exc

    if not isinstance(payload, dict):
        raise TokenVerificationError("Invalid token")
    if payload.get("role") != USER_ROLE:
        raise TokenVerificationError("Invalid token role")
    if not is_uuid(payload.get("sub")):
        raise TokenVerificationError("Token does not contain user ID (sub claim)")
    return payload


def org_claims_from_payload(payload: dict) -> tuple[str, str]:
    """
    Organisation id and app role from signed app_metadata.

    org_id is empty unless it is a UUID. An unknown app role becomes
    ``viewer`` (the least privilege of the three roles the API understands).
    A missing role stays ``recruiter``, which is what existing tokens omit
    when the user was provisioned without an explicit app role.
    """
    meta = payload.get("app_metadata")
    if not isinstance(meta, dict):
        meta = {}
    org_id = str(meta.get("org_id") or "").strip()
    if org_id and not is_uuid(org_id):
        org_id = ""
    role = meta.get("role") or "recruiter"
    if role not in APP_ROLES:
        role = "viewer"
    return org_id, role
