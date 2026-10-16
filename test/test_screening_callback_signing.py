"""Screening results webhook signing (ainm /hiring/screening-callback auth).

ainm verifies X-Execflex-Signature = "sha256=" + HMAC-SHA256(AINM_SERVICE_KEY,
f"{X-Execflex-Timestamp}." + raw body). These tests pin the signer to that
contract and check the signature is only attached when the job asked for it.
"""
import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services import screening_service


class _Resp:
    status_code = 200
    text = "ok"


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _Resp()

    monkeypatch.setattr(screening_service.http_requests, "post", fake_post)
    monkeypatch.setenv("AINM_SERVICE_KEY", "test-shared-key")
    return calls


def test_signed_callback_verifies_with_ainm_algorithm(captured):
    payload = {"source_candidate_id": "app-1", "overall_score": 3.5}
    screening_service._fire_callback("https://ainm.ai/cb", payload, job_id="j1", sign=True)

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


def test_unsigned_callback_is_not_delivered(captured):
    # GDPR-E-005: an unsigned callback (user-created job) is never posted.
    screening_service._fire_callback("https://ainm.ai/cb", {"a": 1}, job_id="j1")
    assert captured == []


def test_callback_outside_allow_list_is_not_delivered(captured):
    screening_service._fire_callback("https://example.com/cb", {"a": 1}, job_id="j1", sign=True)
    assert captured == []


def test_no_key_means_no_delivery(captured, monkeypatch):
    # Without the shared key the payload cannot be signed, so it is not sent.
    monkeypatch.delenv("AINM_SERVICE_KEY", raising=False)
    screening_service._fire_callback("https://ainm.ai/cb", {"a": 1}, job_id="j1", sign=True)
    assert captured == []
