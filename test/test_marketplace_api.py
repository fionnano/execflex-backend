"""Marketplace API — real-path route tests over an in-memory Supabase fake.

Exercises the real route handlers, org auth, store mapping, seeder, and
placement-fee math. The Supabase client is faked; everything else is prod code.
Zero real LLM calls (vetting forced onto the heuristic path), zero real data.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import jwt as pyjwt

os.environ["MARKETPLACE_VETTING_AI"] = "off"  # deterministic scoring in tests


# ── In-memory fake Supabase (supports select/eq/order/limit/insert/update/delete) ──

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

    def range(self, a, b):
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
                r.setdefault("created_at", "2026-07-14T00:00:00+00:00")
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
            removed = len(rows) - len(keep)
            self.store[self.table_name] = keep
            return FakeResult([{"removed": removed}])
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


@pytest.fixture
def client(monkeypatch):
    db = FakeSupabase()
    import config.clients as clients
    monkeypatch.setattr(clients, "supabase_client", db)
    # store.py imports supabase_client lazily via config.clients, so the patch lands.
    from server import app
    app.config["TESTING"] = True
    c = app.test_client()
    c._db = db
    return c


def auth(role="owner"):
    return {"Authorization": f"Bearer {token(role=role)}"}


# ── Seed + browse ────────────────────────────────────────────────────

def test_seed_creates_full_pool(client):
    r = client.post("/api/v1/marketplace/seed", headers=auth())
    assert r.status_code == 201, r.get_data(as_text=True)
    data = r.get_json()["data"]
    assert data["leaders"] == 15
    assert data["opportunities"] == 6
    assert data["introductions"] == 5


def test_browse_returns_only_verified_by_default(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/leaders", headers=auth())
    assert r.status_code == 200
    leaders = r.get_json()["data"]["leaders"]
    # 13 verified, 2 pending in the seed.
    assert len(leaders) == 13
    assert all(l["vetting_status"] == "verified" for l in leaders)
    assert all(l["vetting_score"] is not None for l in leaders)


def test_browse_filter_by_track_and_engagement(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/leaders?track=ml_platform", headers=auth())
    leaders = r.get_json()["data"]["leaders"]
    assert leaders and all(l["track"] == "ml_platform" for l in leaders)
    r2 = client.get("/api/v1/marketplace/leaders?engagement=fractional", headers=auth())
    frac = r2.get_json()["data"]["leaders"]
    assert all(l["engagement"] in ("fractional", "both") for l in frac)


def test_leader_profile_has_vetting_rationale(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    leaders = client.get("/api/v1/marketplace/leaders", headers=auth()).get_json()["data"]["leaders"]
    lid = leaders[0]["id"]
    r = client.get(f"/api/v1/marketplace/leaders/{lid}", headers=auth())
    assert r.status_code == 200
    leader = r.get_json()["data"]
    assert leader["vetting"]["rationale"]
    assert leader["vetting"]["per_competency"]


# ── Supply side: apply + vetting ─────────────────────────────────────

def test_apply_then_vet_verifies_strong_candidate(client):
    apply = client.post("/api/v1/marketplace/leaders", headers=auth(), json={
        "name": "New Leader", "headline": "Head of ML", "track": "ml_platform",
        "skills": ["MLOps"], "seniority": "Head of",
    })
    assert apply.status_code == 201
    lid = apply.get_json()["data"]["id"]
    assert apply.get_json()["data"]["vetting_status"] == "pending"

    qs = client.get("/api/v1/marketplace/vetting/questions?track=ml_platform",
                    headers=auth()).get_json()["data"]["questions"]
    strong = ("I led a team of 10 and cut p99 latency 60% while reducing cost 30%, "
              "owning the incident response and drift detection with SLAs and rollback.")
    responses = [{"question_id": q["id"], "competency": q["competency"],
                  "weight": q["weight"], "text": strong} for q in qs]
    r = client.post(f"/api/v1/marketplace/leaders/{lid}/vetting", headers=auth(),
                    json={"track": "ml_platform", "responses": responses})
    assert r.status_code == 200
    vet = r.get_json()["data"]["vetting"]
    assert vet["status"] == "verified"
    assert vet["score"] >= 70
    # Now appears in the verified pool.
    leaders = client.get("/api/v1/marketplace/leaders", headers=auth()).get_json()["data"]["leaders"]
    assert lid in [l["id"] for l in leaders]


def test_vetting_requires_responses(client):
    apply = client.post("/api/v1/marketplace/leaders", headers=auth(), json={
        "name": "X", "track": "ml_platform"})
    lid = apply.get_json()["data"]["id"]
    r = client.post(f"/api/v1/marketplace/leaders/{lid}/vetting", headers=auth(), json={})
    assert r.status_code == 400


# ── Introductions + placement fee ────────────────────────────────────

def test_request_introduction_creates_row(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    leaders = client.get("/api/v1/marketplace/leaders", headers=auth()).get_json()["data"]["leaders"]
    lid = leaders[0]["id"]
    r = client.post(f"/api/v1/marketplace/leaders/{lid}/introductions", headers=auth(), json={
        "company": {"name": "Acme AI"}, "message": "Keen to talk",
    })
    assert r.status_code == 201, r.get_data(as_text=True)
    intro = r.get_json()["data"]
    assert intro["status"] == "requested"
    assert intro["placement_fee_pct"] == 15.0
    assert intro["leader_id"] == lid


def test_cannot_introduce_unverified_leader(client):
    apply = client.post("/api/v1/marketplace/leaders", headers=auth(), json={
        "name": "Pending Person", "track": "ml_platform"})
    lid = apply.get_json()["data"]["id"]
    r = client.post(f"/api/v1/marketplace/leaders/{lid}/introductions", headers=auth(),
                    json={"company": {"name": "Acme"}})
    assert r.status_code == 400


def test_mark_hired_computes_placement_fee(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    leaders = client.get("/api/v1/marketplace/leaders", headers=auth()).get_json()["data"]["leaders"]
    lid = leaders[0]["id"]
    intro = client.post(f"/api/v1/marketplace/leaders/{lid}/introductions", headers=auth(),
                        json={"company": {"name": "Acme AI"}}).get_json()["data"]
    r = client.patch(f"/api/v1/marketplace/introductions/{intro['id']}", headers=auth(),
                     json={"hired": True, "first_year_comp": 200000, "placement_fee_pct": 15})
    assert r.status_code == 200
    out = r.get_json()["data"]
    assert out["status"] == "hired"
    assert out["hired"] is True
    assert out["placement_fee_amount"] == 30000.0  # 15% of 200k


def test_introductions_pipeline_summary(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/introductions", headers=auth())
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["total"] == 5
    # Seed has one hired intro at 205k * 15% = 30750.
    assert body["summary"]["hired"] == 1
    assert body["summary"]["realised_fees"] == 30750.0


# ── Opportunities / companies ────────────────────────────────────────

def test_opportunities_and_companies(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    opps = client.get("/api/v1/marketplace/opportunities", headers=auth()).get_json()["data"]
    assert opps["total"] == 6
    comps = client.get("/api/v1/marketplace/companies", headers=auth()).get_json()["data"]
    assert comps["total"] >= 5


# ── Auth ─────────────────────────────────────────────────────────────

def test_requires_auth(client):
    assert client.get("/api/v1/marketplace/leaders").status_code == 401


def test_seed_requires_owner(client):
    r = client.post("/api/v1/marketplace/seed",
                    headers={"Authorization": f"Bearer {token(role='viewer')}"})
    assert r.status_code == 403
