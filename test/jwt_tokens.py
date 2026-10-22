"""Build Supabase-shaped user access tokens for tests."""
import time

import jwt as pyjwt

from utils.jwt_verify import expected_audience, expected_issuer, jwt_hmac_secret


def encode_user_token(
    *,
    user_id,
    org_id=None,
    app_role="recruiter",
    secret=None,
    expires_in=3600,
    **overrides,
):
    """
    HS256 token with the claims verification requires.

    ``org_id`` is omitted when empty, matching a user who has not been placed
    in an organisation yet. ``overrides`` replace claims after the defaults,
    so tests can set a wrong ``aud`` or ``iss``.
    """
    now = int(time.time())
    payload = {
        "sub": user_id,
        "aud": expected_audience(),
        "iss": expected_issuer(),
        "exp": now + expires_in,
        "iat": now,
        "role": "authenticated",
    }
    if org_id:
        payload["app_metadata"] = {"org_id": org_id, "role": app_role}
    payload.update(overrides)
    key = secret or jwt_hmac_secret()
    if not key:
        raise RuntimeError("test JWT secret is not configured")
    return pyjwt.encode(payload, key, algorithm="HS256")
