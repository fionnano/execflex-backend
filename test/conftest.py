"""Shared test configuration.

JWT verification fails closed (SEC-E-008): there is no unverified decode
path any more, so every test that mints a bearer token needs a secret to
sign with. Provide one when the environment has none; test modules that set
their own (test_audit_critical_endpoints) override it per test.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_JWT_SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _jwt_secret_for_tests(monkeypatch):
    import config.app_config as app_config
    if not os.getenv("SUPABASE_JWT_SECRET"):
        monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    if not app_config.SUPABASE_JWT_SECRET:
        monkeypatch.setattr(app_config, "SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    # Startup checks: tests never run as production.
    monkeypatch.delenv("RENDER", raising=False)
    yield
