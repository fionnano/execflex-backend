"""
Runtime test for the smoke-test-bypass production guard (security-hardening).

Unlike test_security_verification.py (which greps source), this exercises
get_authenticated_user_id() through a real Flask request context and asserts
behaviour:
  - CI/dev: a valid X-Smoke-Test header authenticates (bypass preserved for CI/CD).
  - production (APP_ENV or FLASK_ENV): the same header is IGNORED — no auth.
"""
import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask
from utils.auth_helpers import get_authenticated_user_id

SMOKE_SECRET = "test-smoke-secret-value"
SMOKE_USER = "smoke-user-123"

app = Flask(__name__)


def _call_with_smoke_header(env: dict):
    """Invoke the auth helper inside a request carrying the smoke header."""
    # Reset the one-shot warning latch so each case behaves deterministically.
    if hasattr(get_authenticated_user_id, "_warned_smoke_prod"):
        delattr(get_authenticated_user_id, "_warned_smoke_prod")
    base = {"SMOKE_TEST_BYPASS_SECRET": SMOKE_SECRET, "SMOKE_TEST_USER_ID": SMOKE_USER}
    base.update(env)
    with patch.dict(os.environ, base, clear=False):
        # Ensure prod flags are absent unless the case sets them.
        for k in ("APP_ENV", "FLASK_ENV"):
            if k not in base:
                os.environ.pop(k, None)
        with app.test_request_context(headers={"X-Smoke-Test": SMOKE_SECRET}):
            return get_authenticated_user_id()


class TestSmokeBypassGuard:
    def test_bypass_authenticates_in_ci_dev(self):
        """CI/CD must still work — the bypass authenticates when not in production."""
        user_id, error = _call_with_smoke_header({"APP_ENV": "dev"})
        assert user_id == SMOKE_USER
        assert error is None

    def test_bypass_blocked_when_app_env_production(self):
        user_id, error = _call_with_smoke_header({"APP_ENV": "production"})
        assert user_id is None, "Smoke bypass MUST NOT authenticate in production"
        assert error is not None

    def test_bypass_blocked_when_flask_env_production(self):
        user_id, error = _call_with_smoke_header({"FLASK_ENV": "production"})
        assert user_id is None, "Smoke bypass MUST NOT authenticate in production"
        assert error is not None

    def test_no_smoke_header_never_authenticates(self):
        """Sanity: without the header, no bypass regardless of env."""
        if hasattr(get_authenticated_user_id, "_warned_smoke_prod"):
            delattr(get_authenticated_user_id, "_warned_smoke_prod")
        with patch.dict(os.environ, {
            "SMOKE_TEST_BYPASS_SECRET": SMOKE_SECRET,
            "SMOKE_TEST_USER_ID": SMOKE_USER,
        }, clear=False):
            os.environ.pop("APP_ENV", None)
            os.environ.pop("FLASK_ENV", None)
            with app.test_request_context():  # no header
                user_id, error = get_authenticated_user_id()
        assert user_id is None
        assert error is not None
