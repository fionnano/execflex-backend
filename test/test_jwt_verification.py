"""
Supabase access tokens must be verified, and unsigned requests must fail closed.

Covers a valid token, and expired, tampered, wrong-audience, and missing tokens.
Also covers the algorithm allow-list, issuer, a missing signing key, Stripe and
Twilio webhooks, the job-processing cron, and the voice-stream token.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

import jwt as pyjwt
import pytest
from flask import Flask, jsonify, request

from jwt_tokens import encode_user_token
from utils.jwt_verify import TokenVerificationError, verify_supabase_access_token
from utils.auth_helpers import get_authenticated_user_id, require_auth
from services.api.auth import get_org_context, require_org

USER = str(uuid.uuid4())
ORG = str(uuid.uuid4())
OTHER_ORG = str(uuid.uuid4())


def _b64(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def auth_app():
    app = Flask(__name__)

    @app.route("/me")
    @require_auth
    def me():
        return jsonify({"user_id": request.environ["authenticated_user_id"]})

    @app.route("/org", methods=["GET", "POST"])
    @require_org()
    def org():
        ctx = get_org_context()
        body = request.get_json(silent=True) or {}
        return jsonify({
            "user_id": ctx.user_id,
            "org_id": ctx.org_id,
            "role": ctx.role,
            "body_org": body.get("organization_id"),
        })

    app.config["TESTING"] = True
    return app


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


class TestAccessTokenVerification:
    def test_valid_token_returns_user(self):
        token = encode_user_token(user_id=USER, org_id=ORG, app_role="owner")
        payload = verify_supabase_access_token(token)
        assert payload["sub"] == USER
        assert payload["app_metadata"]["org_id"] == ORG

    def test_expired_token_is_rejected(self):
        token = encode_user_token(user_id=USER, org_id=ORG, expires_in=-3600)
        with pytest.raises(TokenVerificationError, match="expired"):
            verify_supabase_access_token(token)

    def test_tampered_signature_is_rejected(self):
        token = encode_user_token(user_id=USER, org_id=ORG)
        head, body, sig = token.split(".")
        flipped = ("A" if not sig.startswith("A") else "B") + sig[1:]
        with pytest.raises(TokenVerificationError, match="signature"):
            verify_supabase_access_token(f"{head}.{body}.{flipped}")

    def test_tampered_payload_is_rejected(self):
        token = encode_user_token(user_id=USER, org_id=ORG)
        head, body, sig = token.split(".")
        flipped = ("A" if not body.startswith("A") else "B") + body[1:]
        with pytest.raises(TokenVerificationError):
            verify_supabase_access_token(f"{head}.{flipped}.{sig}")

    def test_wrong_audience_is_rejected(self):
        token = encode_user_token(user_id=USER, org_id=ORG, aud="anon")
        with pytest.raises(TokenVerificationError, match="audience"):
            verify_supabase_access_token(token)

    def test_wrong_issuer_is_rejected(self):
        token = encode_user_token(user_id=USER, org_id=ORG, iss="https://evil.example/auth/v1")
        with pytest.raises(TokenVerificationError, match="issuer"):
            verify_supabase_access_token(token)

    def test_none_algorithm_is_rejected(self):
        payload = {
            "sub": USER,
            "aud": "authenticated",
            "iss": "https://test-project.supabase.co/auth/v1",
            "exp": int(time.time()) + 600,
            "role": "authenticated",
        }
        token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(payload)}."
        with pytest.raises(TokenVerificationError, match="algorithm"):
            verify_supabase_access_token(token)

    def test_unlisted_algorithm_is_rejected(self):
        payload = {
            "sub": USER,
            "aud": "authenticated",
            "iss": "https://test-project.supabase.co/auth/v1",
            "exp": int(time.time()) + 600,
            "role": "authenticated",
        }
        token = f"{_b64({'alg': 'HS384', 'typ': 'JWT'})}.{_b64(payload)}.sig"
        with pytest.raises(TokenVerificationError, match="algorithm"):
            verify_supabase_access_token(token)

    def test_token_signed_with_a_different_secret_is_rejected(self):
        token = encode_user_token(
            user_id=USER,
            org_id=ORG,
            secret="another-jwt-secret-that-is-long-enough-01",
        )
        with pytest.raises(TokenVerificationError, match="signature"):
            verify_supabase_access_token(token)

    def test_service_role_claim_is_rejected(self):
        token = encode_user_token(user_id=USER, org_id=ORG, role="service_role")
        with pytest.raises(TokenVerificationError, match="role"):
            verify_supabase_access_token(token)

    def test_missing_secret_rejects_even_a_well_formed_token(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        import config.app_config as app_config
        monkeypatch.setattr(app_config, "SUPABASE_JWT_SECRET", None)
        token = pyjwt.encode(
            {
                "sub": USER,
                "aud": "authenticated",
                "iss": "https://test-project.supabase.co/auth/v1",
                "exp": int(time.time()) + 600,
                "role": "authenticated",
            },
            "test-supabase-jwt-secret-do-not-use-prod-01",
            algorithm="HS256",
        )
        with pytest.raises(TokenVerificationError, match="not configured"):
            verify_supabase_access_token(token)

    def test_short_secret_is_ignored(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "too-short")
        from utils.jwt_verify import jwt_hmac_secret
        assert jwt_hmac_secret() is None

    def test_asymmetric_token_uses_jwks_not_the_hmac_secret(self, monkeypatch):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        now = int(time.time())
        payload = {
            "sub": USER,
            "aud": "authenticated",
            "iss": "https://test-project.supabase.co/auth/v1",
            "exp": now + 600,
            "iat": now,
            "role": "authenticated",
            "app_metadata": {"org_id": ORG, "role": "owner"},
        }
        token = pyjwt.encode(payload, private_pem, algorithm="RS256")
        monkeypatch.setattr("utils.jwt_verify._asymmetric_signing_key", lambda _token: public_pem)
        assert verify_supabase_access_token(token)["sub"] == USER

        # Algorithm confusion: HMAC-sign with the public key material and claim HS256.
        # Verification must use the configured HMAC secret, not that material.
        public_material = "".join(public_pem.decode().split("-----")[2].split())
        confused = pyjwt.encode(payload, public_material, algorithm="HS256")
        with pytest.raises(TokenVerificationError, match="signature"):
            verify_supabase_access_token(confused)


class TestRouteAuth:
    def test_missing_token_is_401(self, auth_app):
        client = auth_app.test_client()
        resp = client.get("/me")
        assert resp.status_code == 401
        resp_org = client.get("/org")
        assert resp_org.status_code == 401

    def test_valid_token_reaches_only_its_own_org(self, auth_app):
        client = auth_app.test_client()
        token = encode_user_token(user_id=USER, org_id=ORG, app_role="owner")
        resp = client.post(
            "/org",
            json={"organization_id": OTHER_ORG},
            headers=_bearer(token),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["user_id"] == USER
        assert body["org_id"] == ORG
        assert body["body_org"] == OTHER_ORG
        assert body["org_id"] != body["body_org"]

    def test_expired_token_is_401(self, auth_app):
        client = auth_app.test_client()
        token = encode_user_token(user_id=USER, org_id=ORG, expires_in=-3600)
        resp = client.get("/me", headers=_bearer(token))
        assert resp.status_code == 401
        assert "expired" in resp.get_json()["error"]

    def test_wrong_audience_is_401(self, auth_app):
        client = auth_app.test_client()
        token = encode_user_token(user_id=USER, org_id=ORG, aud="public")
        assert client.get("/org", headers=_bearer(token)).status_code == 401

    def test_helper_reports_expiry(self, auth_app):
        token = encode_user_token(user_id=USER, org_id=ORG, expires_in=-3600)
        with auth_app.test_request_context(headers=_bearer(token)):
            user_id, error = get_authenticated_user_id()
        assert user_id is None
        assert error == "Token has expired"


class TestFailClosedConfig:
    def test_production_refuses_to_start_without_a_verification_key(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        monkeypatch.delenv("SUPABASE_JWT_JWKS", raising=False)
        import config.app_config as app_config
        monkeypatch.setattr(app_config, "SUPABASE_JWT_SECRET", None)
        with pytest.raises(RuntimeError, match="Refusing to start"):
            app_config.validate_config()

    def test_verification_is_never_disabled_in_source(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for rel in ("utils/auth_helpers.py", "services/api/auth.py", "utils/jwt_verify.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as handle:
                source = handle.read()
            assert "verify_signature" not in source or 'verify_signature": True' in source
            assert "verify_signature\": False" not in source
            assert "verify_signature=False" not in source


class TestWebhooksAndCron:
    def test_twilio_missing_token_is_rejected_in_dev(self, monkeypatch):
        import utils.twilio_helpers as twilio_helpers
        monkeypatch.setenv("APP_ENV", "dev")
        monkeypatch.delenv("TWILIO_ALLOW_UNSIGNED_WEBHOOKS", raising=False)
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        monkeypatch.setattr(twilio_helpers, "TWILIO_AUTH_TOKEN", None)
        assert twilio_helpers.verify_twilio_signature() is False

    def test_twilio_unsigned_flag_is_ignored_in_production(self, monkeypatch):
        import utils.twilio_helpers as twilio_helpers
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("TWILIO_ALLOW_UNSIGNED_WEBHOOKS", "1")
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        monkeypatch.setattr(twilio_helpers, "TWILIO_AUTH_TOKEN", None)
        assert twilio_helpers.verify_twilio_signature() is False

    def test_twilio_valid_signature_is_accepted(self, monkeypatch):
        from twilio.request_validator import RequestValidator
        import utils.twilio_helpers as twilio_helpers

        token = "twilio-auth-token-for-tests-32ch"
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
        monkeypatch.delenv("TWILIO_ALLOW_UNSIGNED_WEBHOOKS", raising=False)
        monkeypatch.setenv("APP_ENV", "test")
        url = "https://execflex.example/voice/status"
        params = {"CallSid": "CA123", "CallStatus": "completed"}
        signature = RequestValidator(token).compute_signature(url, params)
        app = Flask(__name__)
        with app.test_request_context(
            "/voice/status",
            method="POST",
            data=params,
            base_url="https://execflex.example",
            headers={"X-Twilio-Signature": signature},
        ):
            assert twilio_helpers.verify_twilio_signature() is True
        with app.test_request_context(
            "/voice/status",
            method="POST",
            data=params,
            base_url="https://execflex.example",
            headers={"X-Twilio-Signature": "not-the-signature"},
        ):
            assert twilio_helpers.verify_twilio_signature() is False

    def test_stripe_webhook_refuses_when_secret_missing(self, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        from server import app
        client = app.test_client()
        resp = client.post(
            "/billing/webhook",
            data=b'{"type":"payment_intent.succeeded","data":{"object":{}}}',
            content_type="application/json",
        )
        assert resp.status_code == 503

    def test_stripe_webhook_rejects_bad_signature_and_accepts_a_good_one(self, monkeypatch):
        secret = "whsec_test_webhook_secret_value_32"
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
        from server import app
        client = app.test_client()
        payload = b'{"id":"evt_test","object":"event","type":"ping","data":{"object":{}}}'
        bad = client.post(
            "/billing/webhook",
            data=payload,
            headers={"Stripe-Signature": "t=1,v1=deadbeef"},
            content_type="application/json",
        )
        assert bad.status_code == 400

        timestamp = int(time.time())
        signed = f"{timestamp}.".encode() + payload
        mac = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        good = client.post(
            "/billing/webhook",
            data=payload,
            headers={"Stripe-Signature": f"t={timestamp},v1={mac}"},
            content_type="application/json",
        )
        assert good.status_code == 200

    def test_process_jobs_requires_a_configured_secret(self, monkeypatch):
        monkeypatch.delenv("PROCESS_JOBS_SECRET", raising=False)
        monkeypatch.delenv("AINM_SERVICE_KEY", raising=False)
        from server import app
        client = app.test_client()
        missing = client.post("/onboarding/process-jobs")
        assert missing.status_code == 503

        monkeypatch.setenv("PROCESS_JOBS_SECRET", "cron-secret-for-tests")
        denied = client.post("/onboarding/process-jobs", headers={"X-Cron-Key": "nope"})
        assert denied.status_code == 401

        called = {}

        def _fake_process(limit=10):
            called["limit"] = limit
            return 0

        import routes.onboarding as onboarding
        monkeypatch.setattr(onboarding, "process_queued_jobs", _fake_process)
        ok = client.post(
            "/onboarding/process-jobs?limit=3",
            headers={"X-Cron-Key": "cron-secret-for-tests"},
        )
        assert ok.status_code == 200
        assert called["limit"] == 3
        assert ok.get_json()["processed"] == 0

    def test_voice_stream_token_is_bound_to_the_job(self, monkeypatch):
        from utils.voice_stream_auth import make_stream_token, verify_stream_token
        monkeypatch.setenv("VOICE_STREAM_SIGNING_SECRET", "voice-stream-signing-secret-32")
        monkeypatch.delenv("TWILIO_ALLOW_UNSIGNED_WEBHOOKS", raising=False)
        job_id = str(uuid.uuid4())
        token = make_stream_token(job_id)
        assert verify_stream_token(job_id, token) is True
        assert verify_stream_token(str(uuid.uuid4()), token) is False
        assert verify_stream_token(job_id, None) is False
        head, sig = token.split(".")
        assert verify_stream_token(job_id, f"{head}.{'0' * len(sig)}") is False

    def test_voice_stream_refuses_without_a_signing_key(self, monkeypatch):
        from utils.voice_stream_auth import make_stream_token, verify_stream_token
        monkeypatch.delenv("VOICE_STREAM_SIGNING_SECRET", raising=False)
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("TWILIO_ALLOW_UNSIGNED_WEBHOOKS", raising=False)
        with pytest.raises(RuntimeError):
            make_stream_token(str(uuid.uuid4()))
        assert verify_stream_token(str(uuid.uuid4()), "1.abc") is False


class TestOpportunityAccess:
    def test_another_user_cannot_use_an_opportunity_id(self, monkeypatch):
        from utils.org_scope import caller_can_access_opportunity
        monkeypatch.setattr("utils.org_scope.resolve_caller_org_id", lambda uid: "org-caller")
        monkeypatch.setattr("utils.auth_helpers.is_user_admin", lambda uid: False)
        own = {"created_by_user_id": "user-a", "organization_id": "org-caller"}
        foreign = {"created_by_user_id": "user-a", "organization_id": "org-other"}
        assert caller_can_access_opportunity("user-a", foreign) is True
        assert caller_can_access_opportunity("user-b", own) is True
        assert caller_can_access_opportunity("user-b", foreign) is False
        assert caller_can_access_opportunity("service:ainm", foreign) is True
