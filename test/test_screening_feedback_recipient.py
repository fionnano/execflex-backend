"""Art 86 feedback must go only to the email on record, never a supplied one.

POST /screening/feedback-request is unauthenticated. It used to look the
candidate up by phone and then email the scores to whatever address the
caller supplied, so anyone holding a candidate's phone number could receive
that candidate's screening results (2026-10-15 HR coverage audit, security
finding 1).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from flask import Flask

import config.clients
import modules.email_sender
from routes import screening_bp
import routes.screening  # noqa: F401  (registers the view functions)


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self._insert = db, table, [], None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def insert(self, row):
        self._insert = row
        return self

    def execute(self):
        if self._insert is not None:
            self.db.inserts.setdefault(self.table, []).append(self._insert)
            return type("R", (), {"data": [self._insert]})()
        rows = [
            r for r in self.db.rows.get(self.table, [])
            if all(r.get(c) == v for c, v in self.filters)
        ]
        return type("R", (), {"data": rows})()


class _FakeSupabase:
    def __init__(self, rows):
        self.rows, self.inserts = rows, {}

    def table(self, name):
        return _Query(self, name)


SCORES = [{"question": "Q1", "competency": "C", "score": 2}]


def _job(**ctx_extra):
    ctx = {"candidate_name": "Vic Tim", "role_title": "Engineer", "company_name": "Co"}
    ctx.update(ctx_extra)
    return {
        "id": "job-1", "interaction_id": "ix-1", "phone_e164": "+353861111111",
        "user_id": None, "artifacts": {"screening_context": ctx},
    }


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_send(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(modules.email_sender, "send_screening_feedback_email", fake_send)
    return calls


def _client(monkeypatch, rows):
    fake = _FakeSupabase(rows)
    monkeypatch.setattr(config.clients, "supabase_client", fake)
    app = Flask(__name__)
    app.register_blueprint(screening_bp)
    return app.test_client(), fake


def _rows(job, identities=()):
    return {
        "outbound_call_jobs": [job],
        "interactions": [{"id": "ix-1", "screening_scores": SCORES, "screening_recommendation": "reject"}],
        "channel_identities": list(identities),
    }


def test_phone_lookup_never_sends_to_supplied_email(monkeypatch, sent):
    client, _ = _client(monkeypatch, _rows(_job(candidate_email="victim@example.com")))
    resp = client.post("/screening/feedback-request",
                       json={"phone": "+353861111111", "email": "attacker@evil.test"})
    assert resp.status_code == 200
    assert [c["candidate_email"] for c in sent] == ["victim@example.com"]
    # The response must not disclose the address on record either.
    assert "victim@example.com" not in resp.get_data(as_text=True)


def test_no_email_on_record_sends_nothing(monkeypatch, sent):
    client, fake = _client(monkeypatch, _rows(_job()))
    resp = client.post("/screening/feedback-request",
                       json={"phone": "+353861111111", "email": "attacker@evil.test"})
    assert resp.status_code == 422
    assert resp.get_json()["status"] == "no_email_on_record"
    assert sent == []
    assert fake.inserts["screening_feedback_requests"][0]["status"] == "failed"


def test_email_on_record_from_platform_user_identity(monkeypatch, sent):
    job = _job()
    job["user_id"] = "user-1"
    identities = [{"channel": "email", "user_id": "user-1", "value": "Cand@Example.com"}]
    client, _ = _client(monkeypatch, _rows(job, identities))
    resp = client.post("/screening/feedback-request",
                       json={"phone": "+353861111111", "email": "attacker@evil.test"})
    assert resp.status_code == 200
    assert [c["candidate_email"] for c in sent] == ["cand@example.com"]


def test_email_lookup_sends_to_that_registered_email(monkeypatch, sent):
    job = _job()
    job["user_id"] = "user-1"
    job["phone_e164"] = "+353869999999"
    identities = [{"channel": "email", "user_id": "user-1", "value": "cand@example.com"}]
    client, _ = _client(monkeypatch, _rows(job, identities))
    resp = client.post("/screening/feedback-request", json={"email": "cand@example.com"})
    assert resp.status_code == 200
    assert [c["candidate_email"] for c in sent] == ["cand@example.com"]


def test_unknown_candidate_404_and_nothing_sent(monkeypatch, sent):
    client, _ = _client(monkeypatch, _rows(_job(candidate_email="victim@example.com")))
    resp = client.post("/screening/feedback-request", json={"phone": "+353860000000"})
    assert resp.status_code == 404
    assert sent == []
