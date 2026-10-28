"""Auth lock for POST /onboarding/process-jobs.

The route places real Twilio calls. It must refuse unless PROCESS_JOBS_SECRET
is configured and the caller sends that value in X-Process-Jobs-Secret.
Comparison is hmac.compare_digest. A Supabase JWT or the Ainm X-Service-Key
must not be accepted: those credentials are wider than this trigger.
"""
import hmac
import os
import sys

import jwt as pyjwt
import pytest

# config.clients creates the Supabase client at import. Give it a well-formed
# dummy key before any project import so collection does not need production.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault(
    "SUPABASE_SERVICE_KEY",
    pyjwt.encode({"role": "service_role", "iss": "supabase"}, "secret", algorithm="HS256"),
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask

from routes import onboarding_bp
from services import onboarding_service
from utils.auth_helpers import PROCESS_JOBS_HEADER


SECRET = "process-jobs-test-secret"
HEADER = {PROCESS_JOBS_HEADER: SECRET}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PROCESS_JOBS_SECRET", SECRET)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(onboarding_bp)
    return app.test_client()


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_process(limit=10):
        seen.append(limit)
        return 2

    monkeypatch.setattr(onboarding_service, "process_queued_jobs", fake_process)
    # The route binds the name at import time.
    monkeypatch.setattr("routes.onboarding.process_queued_jobs", fake_process)
    return seen


def test_unconfigured_secret_is_503_and_does_not_process(client, calls, monkeypatch):
    monkeypatch.delenv("PROCESS_JOBS_SECRET", raising=False)
    resp = client.post("/onboarding/process-jobs", headers=HEADER)
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["ok"] is False
    assert SECRET not in resp.get_data(as_text=True)
    assert calls == []


def test_blank_secret_is_503(client, calls, monkeypatch):
    monkeypatch.setenv("PROCESS_JOBS_SECRET", "   ")
    resp = client.post("/onboarding/process-jobs", headers=HEADER)
    assert resp.status_code == 503
    assert calls == []


def test_missing_header_is_401(client, calls):
    resp = client.post("/onboarding/process-jobs")
    assert resp.status_code == 401
    assert calls == []


def test_empty_header_is_401(client, calls):
    resp = client.post("/onboarding/process-jobs", headers={PROCESS_JOBS_HEADER: ""})
    assert resp.status_code == 401
    assert calls == []


def test_wrong_header_is_403_and_uses_compare_digest(client, calls, monkeypatch):
    compared = {}
    real = hmac.compare_digest

    def spy(a, b):
        compared["args"] = (a, b)
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    resp = client.post(
        "/onboarding/process-jobs",
        headers={PROCESS_JOBS_HEADER: "not-the-secret"},
    )
    assert resp.status_code == 403
    assert calls == []
    assert compared["args"] == (b"not-the-secret", SECRET.encode("utf-8"))
    assert "not-the-secret" not in resp.get_data(as_text=True)
    assert SECRET not in resp.get_data(as_text=True)


def test_ainm_service_key_and_bearer_jwt_are_rejected(client, calls, monkeypatch):
    monkeypatch.setenv("AINM_SERVICE_KEY", SECRET)
    resp = client.post(
        "/onboarding/process-jobs",
        headers={"X-Service-Key": SECRET, "Authorization": "Bearer some-jwt"},
    )
    assert resp.status_code == 401
    assert calls == []


def test_correct_header_processes_and_passes_limit(client, calls):
    resp = client.post("/onboarding/process-jobs?limit=4", headers=HEADER)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["processed"] == 2
    assert calls == [4]


def test_header_name_is_case_insensitive(client, calls):
    resp = client.post(
        "/onboarding/process-jobs",
        headers={"x-process-jobs-secret": SECRET},
    )
    assert resp.status_code == 200
    assert calls == [10]


def test_invalid_limit_does_not_process(client, calls):
    resp = client.post("/onboarding/process-jobs?limit=nope", headers=HEADER)
    assert resp.status_code == 400
    assert calls == []


def test_limit_above_100_does_not_process(client, calls):
    resp = client.post("/onboarding/process-jobs?limit=101", headers=HEADER)
    assert resp.status_code == 400
    assert calls == []


def test_different_length_secret_is_403(client, calls):
    resp = client.post(
        "/onboarding/process-jobs",
        headers={PROCESS_JOBS_HEADER: SECRET + "x"},
    )
    assert resp.status_code == 403
    assert calls == []
