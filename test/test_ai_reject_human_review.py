"""An AI "reject" must not reach the candidate before a human confirms it.

The candidate portal used to map an AI reject straight to "not_progressed",
and the Art 86 feedback email said "Not proceeding at this stage", so the
candidate learned an automated rejection before any recruiter reviewed it —
contradicting the published bias policy ("All AI scores reviewed by human
recruiter before any hiring decision") and GDPR Art. 22 (2026-10-15 HR
coverage audit §5).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from flask import Flask

import config.clients
import modules.email_sender
import utils.auth_helpers
from routes import screening_bp
import routes.screening  # noqa: F401  (registers the view functions)
from services.compliance.human_review import (
    PENDING_HUMAN_REVIEW,
    candidate_facing_outcome,
)


# ── Fake Supabase ───────────────────────────────────────────────────

class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self._update = db, table, [], None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def update(self, values):
        self._update = values
        return self

    def _matches(self):
        return [r for r in self.db.rows.get(self.table, [])
                if all(r.get(c) == v for c, v in self.filters)]

    def execute(self):
        rows = self._matches()
        if self._update is not None:
            for r in rows:
                r.update(self._update)
        return type("R", (), {"data": rows})()


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Query(self, name)


def _rows(ai_rec="reject", human_review=None, job_user_id=None):
    artifacts = {"screening_context": {"candidate_name": "Ann", "role_title": "Engineer",
                                       "company_name": "Co"}}
    if human_review is not None:
        artifacts["human_review"] = human_review
    return {
        "outbound_call_jobs": [{"id": "job-1", "interaction_id": "ix-1",
                                "user_id": job_user_id, "artifacts": artifacts}],
        "interactions": [{"id": "ix-1", "candidate_token": "tok-1",
                          "screening_recommendation": ai_rec,
                          "created_at": datetime.now(timezone.utc).isoformat()}],
    }


def _client(monkeypatch, rows, auth_user=None):
    fake = _FakeSupabase(rows)
    monkeypatch.setattr(config.clients, "supabase_client", fake)
    monkeypatch.setattr(utils.auth_helpers, "get_authenticated_user_id",
                        lambda: (auth_user, None if auth_user else "no auth"))
    app = Flask(__name__)
    app.register_blueprint(screening_bp)
    return app.test_client(), fake


# ── candidate_facing_outcome ────────────────────────────────────────

def test_ai_reject_without_human_is_pending():
    assert candidate_facing_outcome("reject") == PENDING_HUMAN_REVIEW
    assert candidate_facing_outcome("REJECT", {}) == PENDING_HUMAN_REVIEW


def test_human_decision_wins():
    assert candidate_facing_outcome("reject", {"decision": "reject"}) == "reject"
    assert candidate_facing_outcome("reject", {"decision": "proceed"}) == "proceed"


def test_non_adverse_ai_outcomes_pass_through():
    for rec in ("strong_proceed", "proceed", "hold", "incomplete", None):
        assert candidate_facing_outcome(rec) == rec


# ── Candidate portal ────────────────────────────────────────────────

def _portal_status(monkeypatch, rows):
    client, _ = _client(monkeypatch, rows)
    resp = client.get("/screening/candidate-status?token=tok-1")
    assert resp.status_code == 200
    return resp.get_json()["status"]


def test_portal_shows_under_review_for_unreviewed_ai_reject(monkeypatch):
    assert _portal_status(monkeypatch, _rows("reject")) == "under_review"


def test_portal_shows_not_progressed_once_recruiter_confirms(monkeypatch):
    rows = _rows("reject", human_review={"decision": "reject", "reviewer_id": "r-1"})
    assert _portal_status(monkeypatch, rows) == "not_progressed"


def test_portal_reflects_recruiter_override(monkeypatch):
    rows = _rows("reject", human_review={"decision": "proceed", "reviewer_id": "r-1"})
    assert _portal_status(monkeypatch, rows) == "progressed"


# ── Feedback email ──────────────────────────────────────────────────

@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(modules.email_sender, "EMAIL_ADDRESS", "noreply@example.com")
    monkeypatch.setattr(modules.email_sender, "EMAIL_PASSWORD", "x")
    monkeypatch.setattr(modules.email_sender, "_send_message", sent.append)
    return sent


def _feedback(**kw):
    return modules.email_sender.send_screening_feedback_email(
        candidate_email="ann@example.com", candidate_name="Ann", role_title="Engineer",
        company_name="Co", overall_score=1.5,
        scores=[{"question": "Q1", "score": 1}], recommendation="reject", **kw)


def test_feedback_email_never_says_rejected_without_human(outbox):
    assert _feedback() is True
    body = outbox[0].get_body(("plain",)).get_content()
    assert "Not proceeding" not in body
    assert "Under review" in body


def test_feedback_email_states_rejection_once_human_confirmed(outbox):
    assert _feedback(human_confirmed=True) is True
    body = outbox[0].get_body(("plain",)).get_content()
    assert "Not proceeding at this stage" in body


# ── Recruiter confirmation endpoint ─────────────────────────────────

def test_owner_confirms_reject(monkeypatch):
    client, fake = _client(monkeypatch, _rows("reject", job_user_id="user-1"), auth_user="user-1")
    resp = client.post("/screening/job-1/human-review",
                       json={"decision": "reject", "reason": "No production ML experience"})
    assert resp.status_code == 200
    review = fake.rows["outbound_call_jobs"][0]["artifacts"]["human_review"]
    assert review["decision"] == "reject"
    assert review["reviewer_id"] == "user-1"
    assert review["ai_recommendation"] == "reject"
    assert review["human_override"] is False
    # screening_context is preserved alongside the review
    assert "screening_context" in fake.rows["outbound_call_jobs"][0]["artifacts"]


def test_reject_requires_reason(monkeypatch):
    client, fake = _client(monkeypatch, _rows("reject", job_user_id="user-1"), auth_user="user-1")
    resp = client.post("/screening/job-1/human-review", json={"decision": "reject"})
    assert resp.status_code == 400
    assert "human_review" not in fake.rows["outbound_call_jobs"][0]["artifacts"]


def test_override_to_proceed_is_recorded(monkeypatch):
    client, fake = _client(monkeypatch, _rows("reject", job_user_id="user-1"), auth_user="user-1")
    resp = client.post("/screening/job-1/human-review", json={"decision": "proceed"})
    assert resp.status_code == 200
    assert fake.rows["outbound_call_jobs"][0]["artifacts"]["human_review"]["human_override"] is True


def test_other_user_cannot_review(monkeypatch):
    client, fake = _client(monkeypatch, _rows("reject", job_user_id="user-1"), auth_user="user-2")
    resp = client.post("/screening/job-1/human-review",
                       json={"decision": "reject", "reason": "Not a fit for the role"})
    assert resp.status_code == 404
    assert "human_review" not in fake.rows["outbound_call_jobs"][0]["artifacts"]


def test_unauthenticated_rejected(monkeypatch):
    client, _ = _client(monkeypatch, _rows("reject"))
    resp = client.post("/screening/job-1/human-review",
                       json={"decision": "reject", "reason": "Not a fit for the role"})
    assert resp.status_code == 401


def test_service_caller_must_name_reviewer(monkeypatch):
    client, _ = _client(monkeypatch, _rows("reject"), auth_user="service:ainm")
    resp = client.post("/screening/job-1/human-review",
                       json={"decision": "reject", "reason": "Not a fit for the role"})
    assert resp.status_code == 400


def test_service_caller_records_named_reviewer(monkeypatch):
    client, fake = _client(monkeypatch, _rows("reject"), auth_user="service:ainm")
    resp = client.post("/screening/job-1/human-review",
                       json={"decision": "reject", "reason": "Not a fit for the role",
                             "reviewer": "ainm-user-42"})
    assert resp.status_code == 200
    review = fake.rows["outbound_call_jobs"][0]["artifacts"]["human_review"]
    assert review["reviewer_id"] == "ainm-user-42"
    assert review["reviewed_via"] == "service"


def test_invalid_decision(monkeypatch):
    client, _ = _client(monkeypatch, _rows("reject", job_user_id="user-1"), auth_user="user-1")
    resp = client.post("/screening/job-1/human-review", json={"decision": "maybe"})
    assert resp.status_code == 400
