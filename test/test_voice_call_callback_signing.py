"""Voice-call results webhook signing (onboarding / reference / exit interview).

ainm verifies X-Execflex-Signature = "sha256=" + HMAC-SHA256(AINM_SERVICE_KEY,
f"{X-Execflex-Timestamp}." + raw body) on its voice-call webhooks, the same
contract as the screening callback. These tests pin the signer to that
contract, check it is only attached when the job asked for it, and that only
service callers get signed jobs.
"""
import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from flask import Flask

from routes import voice_calls
from services import voice_call_service


class _Resp:
    status_code = 200
    text = "ok"


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _Resp()

    monkeypatch.setattr(voice_call_service.http_requests, "post", fake_post)
    monkeypatch.setenv("AINM_SERVICE_KEY", "test-shared-key")
    return calls


def test_signed_callback_verifies_with_ainm_algorithm(captured):
    payload = {"source_user_id": "u-1", "summary": "left for growth"}
    voice_call_service._fire_callback("https://ainm.ai/cb", payload, "j1", sign=True)

    _, kwargs = captured[0]
    body = kwargs["data"]
    headers = kwargs["headers"]
    assert json.loads(body) == payload
    ts = headers["X-Execflex-Timestamp"]
    expected = "sha256=" + hmac.new(
        b"test-shared-key", f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()
    assert headers["X-Execflex-Signature"] == expected
    # The shared key itself must never be sent to the (caller-supplied) URL.
    assert "test-shared-key" not in json.dumps(headers)
    assert "X-Service-Key" not in headers


def test_unsigned_or_off_list_callback_is_not_delivered(captured):
    # GDPR-E-005: unsigned callbacks and non-allow-listed hosts are never posted.
    voice_call_service._fire_callback("https://ainm.ai/cb", {"a": 1}, "j1")
    voice_call_service._fire_callback("https://example.com/cb", {"a": 1}, "j1", sign=True)
    assert captured == []


class _Table:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def insert(self, row):
        self.store.setdefault(self.name, []).append(row)
        return self

    def execute(self):
        class R:
            data = [{"id": f"{self.name}-id"}]
        return R()


class _Supabase:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return _Table(self.rows, name)


@pytest.mark.parametrize("sign", [True, False])
def test_job_records_sign_flag(monkeypatch, sign):
    sb = _Supabase()
    monkeypatch.setattr(voice_call_service, "supabase_client", sb)
    voice_call_service.create_exit_interview_call_job(
        employee_phone="+353861234567", employee_name="A", company_name="C",
        role_title="R", tenure="1 year", manager_name="M",
        callback_url="https://ainm.ai/cb", source_user_id="u-1", sign_callback=sign,
    )
    ctx = sb.rows["outbound_call_jobs"][0]["artifacts"]["screening_context"]
    assert ctx["sign_callback"] is sign


@pytest.mark.parametrize("user_id,expected", [
    ("service:ainm", True),
    ("3f1c-user-uuid", False),
    (None, False),
])
def test_only_service_callers_get_signed_jobs(user_id, expected):
    app = Flask(__name__)
    environ = {"authenticated_user_id": user_id} if user_id else {}
    with app.test_request_context("/voice-call/exit-interview", environ_base=environ):
        assert voice_calls._is_service_caller() is expected
