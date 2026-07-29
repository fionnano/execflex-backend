"""ainm AI Act Check — real-path route tests over an in-memory Supabase fake.

Exercises the real route handlers, org auth, engine (deterministic path), store
mapping, tenant isolation, GDPR, validation, and rate limiting. Zero LLM calls
(AIACT_AI unset → deterministic), zero real data.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import jwt as pyjwt

os.environ.pop("AIACT_AI", None)  # deterministic engine in tests


# ── In-memory fake Supabase ──────────────────────────────────────────────────

class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._insert = None
        self._update = None
        self._delete = False

    def select(self, *cols, count=None):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values):
        self._update = values
        return self

    def delete(self):
        self._delete = True
        return self

    def _matches(self, row):
        return all(row.get(c) == v for c, v in self.filters)

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._insert is not None:
            for r in self._insert:
                r = dict(r)
                r.setdefault("id", str(uuid.uuid4()))
                r.setdefault("created_at", "2026-07-29T00:00:00+00:00")
                rows.append(r)
            return FakeResult(list(self._insert))
        if self._update is not None:
            updated = []
            for r in rows:
                if self._matches(r):
                    r.update(self._update)
                    updated.append(dict(r))
            return FakeResult(updated)
        if self._delete:
            keep = [r for r in rows if not self._matches(r)]
            self.store[self.table_name] = keep
            return FakeResult([{"removed": len(rows) - len(keep)}])
        return FakeResult([dict(r) for r in rows if self._matches(r)])


class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeQuery(self.store, name)


ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
USER_A = str(uuid.uuid4())


def token(org_id=ORG_A, user_id=USER_A, role="owner"):
    from config.app_config import SUPABASE_JWT_SECRET
    payload = {"sub": user_id, "aud": "authenticated",
               "app_metadata": {"org_id": org_id, "role": role}}
    return pyjwt.encode(payload, SUPABASE_JWT_SECRET or "test-secret", algorithm="HS256")


def auth(org_id=ORG_A, user_id=USER_A, role="owner"):
    return {"Authorization": f"Bearer {token(org_id=org_id, user_id=user_id, role=role)}"}


@pytest.fixture
def client(monkeypatch):
    db = FakeSupabase()
    import config.clients as clients
    monkeypatch.setattr(clients, "supabase_client", db)
    import routes.api_v1.aiact as ai
    ai._ip_buckets.clear()
    ai._org_buckets.clear()
    from server import app
    app.config["TESTING"] = True
    c = app.test_client()
    c._db = db
    return c


# Answer sets
HIGH_RISK_HIRING = {
    "system_name": "CV screening tool", "uses_ai": "yes",
    "business_functions": ["hr"], "affects_people": "yes",
    "automated_hiring_decisions": "yes", "in_eu": "yes",
    "human_oversight": "no", "data_governance": "no", "keeps_logs": "no",
    "has_documentation": "no", "candidates_informed": "no",
}


def _create_and_score(client, answers, headers=None):
    headers = headers or auth()
    sysname = answers.get("system_name", "System")
    c = client.post("/api/v1/aiact/assessments", headers=headers,
                    json={"system_name": sysname, "answers": answers})
    assert c.status_code == 201, c.get_data(as_text=True)
    aid = c.get_json()["data"]["id"]
    s = client.post(f"/api/v1/aiact/assessments/{aid}/score", headers=headers, json={})
    return aid, s


# ── Questions ────────────────────────────────────────────────────────────────

def test_questions_returns_stages_and_disclaimer(client):
    r = client.get("/api/v1/aiact/questions", headers=auth())
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert len(data["stages"]) == 4
    assert "not legal advice" in data["disclaimer"].lower()


def test_questions_requires_auth(client):
    assert client.get("/api/v1/aiact/questions").status_code == 401


# ── Scoring: high-risk hiring AI ─────────────────────────────────────────────

def test_high_risk_hiring_classification(client):
    aid, s = _create_and_score(client, HIGH_RISK_HIRING)
    assert s.status_code == 200, s.get_data(as_text=True)
    res = s.get_json()["data"]["result"]
    assert res["risk_classification"] == "High Risk"
    assert res["readiness_score"] <= 60
    # Obligations include the high-risk stack + employment-specific.
    arts = {o["article"] for o in res["obligations"]}
    assert "Article 14" in arts and "Article 9" in arts
    assert any(o["key"] == "annex3_4_employment" for o in res["obligations"])
    # Gaps and recommendations present; disclaimer attached.
    assert res["gaps"] and res["recommendations"]
    assert "not legal advice" in res["disclaimer"].lower()
    assert res["ai_generated"] is False  # deterministic in tests


def test_prohibited_practice_is_unacceptable(client):
    answers = dict(HIGH_RISK_HIRING)
    answers["assigns_social_scores"] = "yes"
    aid, s = _create_and_score(client, answers)
    res = s.get_json()["data"]["result"]
    assert res["risk_classification"] == "Unacceptable Risk"
    assert res["readiness_score"] <= 15
    assert res["prohibited"]["has_hard_stop"] is True


def test_no_ai_is_minimal(client):
    answers = {"system_name": "None", "uses_ai": "no", "business_functions": [],
               "affects_people": "no", "in_eu": "yes", "has_documentation": "not_applicable"}
    aid, s = _create_and_score(client, answers)
    res = s.get_json()["data"]["result"]
    assert res["risk_classification"] == "Minimal Risk"


def test_score_persists_result_and_status(client):
    aid, s = _create_and_score(client, HIGH_RISK_HIRING)
    got = client.get(f"/api/v1/aiact/assessments/{aid}", headers=auth()).get_json()["data"]
    assert got["status"] == "scored"
    assert got["result"]["risk_classification"] == "High Risk"


# ── Tenant isolation ─────────────────────────────────────────────────────────

def test_tenant_isolation(client):
    aid, _ = _create_and_score(client, HIGH_RISK_HIRING, headers=auth(org_id=ORG_A))
    # Org B cannot see org A's assessment.
    assert client.get(f"/api/v1/aiact/assessments/{aid}",
                      headers=auth(org_id=ORG_B, user_id=str(uuid.uuid4()))).status_code == 404
    lst = client.get("/api/v1/aiact/assessments",
                     headers=auth(org_id=ORG_B, user_id=str(uuid.uuid4()))).get_json()["data"]
    assert lst["total"] == 0
    # Org A sees its own.
    mine = client.get("/api/v1/aiact/assessments", headers=auth(org_id=ORG_A)).get_json()["data"]
    assert mine["total"] == 1


# ── GDPR ─────────────────────────────────────────────────────────────────────

def test_gdpr_export_and_delete(client):
    aid, _ = _create_and_score(client, HIGH_RISK_HIRING)
    exp = client.get(f"/api/v1/aiact/assessments/{aid}/export", headers=auth())
    assert exp.status_code == 200
    assert exp.get_json()["data"]["assessment"]["id"] == aid
    dele = client.delete(f"/api/v1/aiact/assessments/{aid}", headers=auth())
    assert dele.status_code == 200 and dele.get_json()["data"]["deleted"] is True
    assert client.get(f"/api/v1/aiact/assessments/{aid}", headers=auth()).status_code == 404


# ── Validation ───────────────────────────────────────────────────────────────

def test_validation_requires_system_name(client):
    r = client.post("/api/v1/aiact/assessments", headers=auth(), json={"answers": {}})
    assert r.status_code == 400


def test_validation_rejects_bad_option(client):
    r = client.post("/api/v1/aiact/assessments", headers=auth(),
                    json={"system_name": "X", "answers": {"uses_ai": "definitely"}})
    assert r.status_code == 400


def test_score_requires_answers(client):
    c = client.post("/api/v1/aiact/assessments", headers=auth(),
                    json={"system_name": "Empty"})
    aid = c.get_json()["data"]["id"]
    s = client.post(f"/api/v1/aiact/assessments/{aid}/score", headers=auth(), json={})
    assert s.status_code == 400


# ── Rate limiting ────────────────────────────────────────────────────────────

def test_score_rate_limit_per_org(client):
    import routes.api_v1.aiact as ai
    ai._org_buckets.clear(); ai._ip_buckets.clear()
    # Create one assessment, then hammer score past the org limit.
    c = client.post("/api/v1/aiact/assessments", headers=auth(),
                    json={"system_name": "RL", "answers": HIGH_RISK_HIRING})
    aid = c.get_json()["data"]["id"]
    from services.aiact.constants import SCORE_ORG_LIMIT
    codes = []
    for _ in range(SCORE_ORG_LIMIT + 1):
        codes.append(client.post(f"/api/v1/aiact/assessments/{aid}/score",
                                 headers=auth(), json={}).status_code)
    assert codes[-1] == 429
    assert codes[0] == 200
