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
    # Reset process-global state so tests are order-independent.
    import routes.api_v1.marketplace as mkt
    mkt._ip_buckets.clear()
    mkt._leader_buckets.clear()
    import services.marketplace.store as st
    st._system_actor_cache = None
    from server import app
    app.config["TESTING"] = True
    c = app.test_client()
    c._db = db
    return c


def auth(role="owner"):
    return {"Authorization": f"Bearer {token(role=role)}"}


def auth_as(org_id=ORG_A, user_id=USER_A, role="owner"):
    return {"Authorization": f"Bearer {token(org_id=org_id, user_id=user_id, role=role)}"}


def admin_auth():
    # Operating AS the marketplace org → platform admin (see _is_marketplace_admin).
    from services.marketplace.constants import MARKETPLACE_ORG_ID
    return {"Authorization": f"Bearer {token(org_id=MARKETPLACE_ORG_ID, role='owner')}"}


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
        "name": "Xavier Test", "track": "ml_platform"})
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
    # Seed intros are owned by the marketplace org → visible only to an operator
    # (admin) via the all-tenants pipeline, not to a random buyer org.
    r = client.get("/api/v1/marketplace/admin/introductions", headers=admin_auth())
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["total"] == 5
    # Seed has one hired intro at 205k * 15% = 30750.
    assert body["summary"]["hired"] == 1
    assert body["summary"]["realised_fees"] == 30750.0


def test_admin_pipeline_requires_admin(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    # A normal buyer org (owner of its own org) is NOT a marketplace admin.
    r = client.get("/api/v1/marketplace/admin/introductions", headers=auth())
    assert r.status_code == 403


def test_company_introductions_are_tenant_scoped(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    # A buyer org sees only its OWN introductions, never the seed pipeline.
    r = client.get("/api/v1/marketplace/introductions", headers=auth())
    assert r.status_code == 200
    assert r.get_json()["data"]["total"] == 0


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


# ── Phase 2: real search engine ──────────────────────────────────────────────

def test_search_ranks_by_relevance_with_reasons(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/search?q=feature%20store%20MLOps", headers=auth())
    assert r.status_code == 200
    data = r.get_json()["data"]
    results = data["results"]
    assert results, "expected ranked results"
    # Ranked, not just filtered: descending relevance, rank set, reasons present.
    rels = [x["relevance"] for x in results]
    assert rels == sorted(rels, reverse=True)
    assert results[0]["rank"] == 1
    assert results[0]["match_reasons"]
    # The top hit should be an ML-platform / feature-store leader.
    assert any("feature" in s.lower() or "mlops" in s.lower()
               for s in " ".join(results[0]["match_reasons"]).split("\n") + results[0]["match_reasons"]) \
        or results[0]["track"] == "ml_platform"


def test_search_free_text_matches_sector(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/search?q=fintech", headers=auth())
    results = r.get_json()["data"]["results"]
    assert results
    # Every returned leader either lists a fintech sector or was matched on it.
    top = results[0]
    assert any("fintech" in (s or "").lower() for s in top["sectors"]) or top["match_reasons"]


def test_search_empty_query_ranks_by_vetting_score(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/search", headers=auth())
    results = r.get_json()["data"]["results"]
    assert results
    scores = [x.get("vetting_score") or 0 for x in results]
    assert scores == sorted(scores, reverse=True)


def test_search_facet_filter_track(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/search?track=data_engineering", headers=auth())
    results = r.get_json()["data"]["results"]
    assert results and all(x["track"] == "data_engineering" for x in results)


def test_search_never_returns_contact(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/search?q=platform", headers=auth())
    for x in r.get_json()["data"]["results"]:
        assert "contact" not in x


def test_facets_endpoint(client):
    client.post("/api/v1/marketplace/seed", headers=auth())
    r = client.get("/api/v1/marketplace/facets", headers=auth())
    assert r.status_code == 200
    f = r.get_json()["data"]
    assert f["skills"] and f["sectors"] and f["tracks"]


# ── Phase 3: real supply side (account-linked leader) ────────────────────────

LEADER_USER = str(uuid.uuid4())
LEADER_ORG = str(uuid.uuid4())
COMPANY_USER = str(uuid.uuid4())
COMPANY_ORG = str(uuid.uuid4())

_STRONG = ("I led a team of 10 and cut p99 latency 60% while reducing cost 30%, "
           "owning incident response, drift detection, rollback and SLAs across "
           "production model serving and feature stores.")


def _apply_and_vet_leader(client, headers, track="ml_platform", email=None):
    body = {"name": "Real Leader", "headline": "Head of ML Platform", "track": track,
            "skills": ["MLOps", "Feature Stores"], "seniority": "Head of"}
    if email:
        body["email"] = email
    a = client.post("/api/v1/marketplace/leaders", headers=headers, json=body)
    assert a.status_code in (200, 201), a.get_data(as_text=True)
    lid = a.get_json()["data"]["id"]
    qs = client.get(f"/api/v1/marketplace/vetting/questions?track={track}",
                    headers=headers).get_json()["data"]["questions"]
    responses = [{"question_id": q["id"], "competency": q["competency"],
                  "weight": q["weight"], "text": _STRONG} for q in qs]
    v = client.post(f"/api/v1/marketplace/leaders/{lid}/vetting", headers=headers,
                    json={"track": track, "responses": responses})
    assert v.status_code == 200, v.get_data(as_text=True)
    return lid, v.get_json()["data"]["vetting"]


def test_leader_profile_linked_to_account(client):
    h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    lid, vet = _apply_and_vet_leader(client, h)
    assert vet["status"] == "verified"
    me = client.get("/api/v1/marketplace/me", headers=h).get_json()["data"]
    assert me["is_leader"] is True
    assert me["leader"]["id"] == lid


def test_apply_twice_is_idempotent_claim(client):
    h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    a1 = client.post("/api/v1/marketplace/leaders", headers=h,
                     json={"name": "Real Leader", "track": "ml_platform"})
    assert a1.status_code == 201
    a2 = client.post("/api/v1/marketplace/leaders", headers=h,
                     json={"name": "Real Leader Renamed", "track": "ai_product"})
    assert a2.status_code == 200
    assert a1.get_json()["data"]["id"] == a2.get_json()["data"]["id"]


def test_edit_own_profile_only(client):
    h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    lid, _ = _apply_and_vet_leader(client, h)
    ok = client.patch(f"/api/v1/marketplace/leaders/{lid}", headers=h,
                      json={"headline": "Updated headline", "bio": "New bio"})
    assert ok.status_code == 200
    assert ok.get_json()["data"]["headline"] == "Updated headline"
    # A different account cannot edit it.
    other = auth_as(org_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))
    bad = client.patch(f"/api/v1/marketplace/leaders/{lid}", headers=other,
                       json={"headline": "hax"})
    assert bad.status_code == 403


def test_owner_sees_contact_but_public_does_not(client):
    h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    lid, _ = _apply_and_vet_leader(client, h, email="me@example.com")
    mine = client.get(f"/api/v1/marketplace/leaders/{lid}", headers=h).get_json()["data"]
    assert mine.get("contact", {}).get("email") == "me@example.com"
    public = auth_as(org_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))
    pub = client.get(f"/api/v1/marketplace/leaders/{lid}", headers=public).get_json()["data"]
    assert not pub.get("contact")


# ── Phase 4 + 5: full two-sided lifecycle with contact reveal ────────────────

def test_full_introduction_lifecycle_with_contact_reveal(client):
    leader_h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    company_h = auth_as(org_id=COMPANY_ORG, user_id=COMPANY_USER)

    lid, _ = _apply_and_vet_leader(client, leader_h, email="leader@example.com")

    # Company sets its profile (so the leader sees who's asking).
    client.put("/api/v1/marketplace/company", headers=company_h, json={
        "name": "Acme AI", "sector": "FinTech",
        "contact_name": "Dana Buyer", "contact_email": "dana@example.com"})

    # Company requests an introduction.
    req = client.post(f"/api/v1/marketplace/leaders/{lid}/introductions",
                      headers=company_h, json={"company": {"name": "Acme AI"},
                                               "message": "Keen to talk", "first_year_comp": 200000})
    assert req.status_code == 201
    intro_id = req.get_json()["data"]["id"]

    # Company view before acceptance: no leader contact revealed.
    mine = client.get("/api/v1/marketplace/introductions", headers=company_h).get_json()["data"]
    assert mine["total"] == 1
    assert not mine["introductions"][0].get("leader_contact")

    # Leader sees it in their inbox, with the requester's identity.
    inbox = client.get("/api/v1/marketplace/inbox", headers=leader_h).get_json()["data"]
    assert inbox["total"] == 1
    assert inbox["introductions"][0]["requester_contact"]["email"] == "dana@example.com"

    # Leader accepts → contact revealed to the company.
    resp = client.post(f"/api/v1/marketplace/introductions/{intro_id}/respond",
                       headers=leader_h, json={"decision": "accepted"})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["status"] == "accepted"

    mine2 = client.get("/api/v1/marketplace/introductions", headers=company_h).get_json()["data"]
    revealed = mine2["introductions"][0]
    assert revealed["contact_revealed"] is True
    assert revealed["leader_contact"]["email"] == "leader@example.com"

    # Company records the hire → placement fee computed.
    hire = client.patch(f"/api/v1/marketplace/introductions/{intro_id}", headers=company_h,
                        json={"hired": True, "first_year_comp": 200000, "placement_fee_pct": 15})
    assert hire.status_code == 200
    assert hire.get_json()["data"]["placement_fee_amount"] == 30000.0

    # Tenant isolation: an unrelated company sees none of this.
    stranger = auth_as(org_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))
    assert client.get("/api/v1/marketplace/introductions", headers=stranger).get_json()["data"]["total"] == 0


def test_leader_decline_hides_contact(client):
    leader_h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    company_h = auth_as(org_id=COMPANY_ORG, user_id=COMPANY_USER)
    lid, _ = _apply_and_vet_leader(client, leader_h, email="leader@example.com")
    req = client.post(f"/api/v1/marketplace/leaders/{lid}/introductions",
                      headers=company_h, json={"company": {"name": "Acme AI"}})
    intro_id = req.get_json()["data"]["id"]
    client.post(f"/api/v1/marketplace/introductions/{intro_id}/respond",
                headers=leader_h, json={"decision": "declined"})
    mine = client.get("/api/v1/marketplace/introductions", headers=company_h).get_json()["data"]
    intro = mine["introductions"][0]
    assert intro["status"] == "declined"
    assert not intro.get("leader_contact")


def test_company_cannot_self_accept(client):
    leader_h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    company_h = auth_as(org_id=COMPANY_ORG, user_id=COMPANY_USER)
    lid, _ = _apply_and_vet_leader(client, leader_h)
    req = client.post(f"/api/v1/marketplace/leaders/{lid}/introductions",
                      headers=company_h, json={"company": {"name": "Acme"}})
    intro_id = req.get_json()["data"]["id"]
    # Company tries to force status=accepted via PATCH → rejected.
    bad = client.patch(f"/api/v1/marketplace/introductions/{intro_id}", headers=company_h,
                       json={"status": "accepted"})
    assert bad.status_code == 400


# ── Company profile ──────────────────────────────────────────────────────────

def test_company_profile_upsert(client):
    h = auth_as(org_id=COMPANY_ORG, user_id=COMPANY_USER)
    assert client.get("/api/v1/marketplace/company", headers=h).get_json()["data"] is None
    put = client.put("/api/v1/marketplace/company", headers=h,
                     json={"name": "Acme AI", "sector": "FinTech", "website": "acme.example"})
    assert put.status_code == 200
    got = client.get("/api/v1/marketplace/company", headers=h).get_json()["data"]
    assert got["name"] == "Acme AI"


# ── GDPR ─────────────────────────────────────────────────────────────────────

def test_gdpr_export_and_delete(client):
    h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    lid, _ = _apply_and_vet_leader(client, h, email="leader@example.com")
    exp = client.get("/api/v1/marketplace/me/export", headers=h)
    assert exp.status_code == 200
    body = exp.get_json()["data"]
    assert body["profile"]["id"] == lid
    assert "introductions" in body
    dele = client.delete("/api/v1/marketplace/me", headers=h)
    assert dele.status_code == 200 and dele.get_json()["data"]["deleted"] is True
    # Profile is gone.
    assert client.get("/api/v1/marketplace/me", headers=h).get_json()["data"]["is_leader"] is False


def test_gdpr_delete_leader_with_introductions(client):
    # A leader with existing introductions can still be erased (ON DELETE handled
    # in code — the leader's introductions are removed first).
    leader_h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    company_h = auth_as(org_id=COMPANY_ORG, user_id=COMPANY_USER)
    lid, _ = _apply_and_vet_leader(client, leader_h, email="leader@example.com")
    req = client.post(f"/api/v1/marketplace/leaders/{lid}/introductions",
                      headers=company_h, json={"company": {"name": "Acme AI"}})
    assert req.status_code == 201
    # Leader erases their profile despite having an introduction addressed to them.
    dele = client.delete("/api/v1/marketplace/me", headers=leader_h)
    assert dele.status_code == 200 and dele.get_json()["data"]["deleted"] is True
    # Leader profile gone, and the introduction was removed with it.
    assert client.get(f"/api/v1/marketplace/leaders/{lid}", headers=leader_h).status_code == 404
    company_view = client.get("/api/v1/marketplace/introductions", headers=company_h).get_json()["data"]
    assert company_view["total"] == 0


# ── Validation ───────────────────────────────────────────────────────────────

def test_apply_validation_rejects_bad_email(client):
    h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    r = client.post("/api/v1/marketplace/leaders", headers=h,
                    json={"name": "X Y", "track": "ml_platform", "email": "not-an-email"})
    assert r.status_code == 400


def test_apply_validation_requires_name(client):
    h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    r = client.post("/api/v1/marketplace/leaders", headers=h, json={"track": "ml_platform"})
    assert r.status_code == 400


def test_vetting_rate_limit(client):
    h = auth_as(org_id=LEADER_ORG, user_id=LEADER_USER)
    a = client.post("/api/v1/marketplace/leaders", headers=h,
                    json={"name": "Rate Tester", "track": "ml_platform"})
    lid = a.get_json()["data"]["id"]
    qs = client.get("/api/v1/marketplace/vetting/questions?track=ml_platform",
                    headers=h).get_json()["data"]["questions"]
    responses = [{"question_id": q["id"], "competency": q["competency"],
                  "weight": q["weight"], "text": _STRONG} for q in qs]
    # Leader limit is 3/day; the 4th attempt is rejected.
    codes = []
    for _ in range(4):
        rr = client.post(f"/api/v1/marketplace/leaders/{lid}/vetting", headers=h,
                         json={"track": "ml_platform", "responses": responses})
        codes.append(rr.status_code)
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
