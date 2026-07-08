"""
Real-path tests for the Aidan-in-console bridge.

Covers the org-scoped phone-screening endpoints added for the ainm Search
console: POST /api/v1/screens/phone, GET /api/v1/screens/<id>/call-status,
GET /api/v1/screens (list), and the candidates serializer.

The Supabase client is replaced with an in-memory fake; everything else —
route handlers, org auth, create_screening_job, get_screening_status, the
read-through sync — is the real production code. The Twilio transport is
"stubbed" only in the sense that no dispatcher worker is running: the test
asserts the outbound_call_jobs row is queued exactly as prod queues it.

All data below is synthetic — no real candidate data.
"""
import uuid
import pytest
import jwt as pyjwt


# ── In-memory fake Supabase ─────────────────────────────────────────

class FakeQuery:
    def __init__(self, store, table_name):
        self.store = store
        self.table_name = table_name
        self.filters = []
        self._count = None
        self._insert_rows = None
        self._update_values = None

    # builder API used by the routes/services under test
    def select(self, *cols, count=None):
        self._count = count
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def or_(self, expr):
        return self  # search fan-out not exercised in these tests

    def like(self, col, pattern):
        self.filters.append(("like", col, pattern))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, vals))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def range(self, a, b):
        return self

    def insert(self, rows):
        self._insert_rows = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values):
        self._update_values = values
        return self

    def _matches(self, row):
        for op, col, val in self.filters:
            if op == "eq" and row.get(col) != val:
                return False
            if op == "in" and row.get(col) not in val:
                return False
            if op == "like":
                prefix = val.rstrip("%")
                if not str(row.get(col) or "").startswith(prefix):
                    return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._insert_rows is not None:
            for r in self._insert_rows:
                r = dict(r)
                r.setdefault("id", str(uuid.uuid4()))
                r.setdefault("created_at", "2026-07-08T00:00:00+00:00")
                rows.append(r)
            return FakeResult(list(self._insert_rows and rows[-len(self._insert_rows):]))
        if self._update_values is not None:
            updated = []
            for r in rows:
                if self._matches(r):
                    r.update(self._update_values)
                    updated.append(dict(r))
            return FakeResult(updated)
        matched = [dict(r) for r in rows if self._matches(r)]
        return FakeResult(matched, count=len(matched) if self._count else None)


class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeQuery(self.store, name)


# ── Fixtures ────────────────────────────────────────────────────────

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
USER_A = str(uuid.uuid4())


def make_token(org_id=ORG_A, user_id=USER_A, role="recruiter"):
    from config.app_config import SUPABASE_JWT_SECRET
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "app_metadata": {"org_id": org_id, "role": role},
    }
    return pyjwt.encode(payload, SUPABASE_JWT_SECRET or "test-secret", algorithm="HS256")


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeSupabase()
    import config.clients as clients
    import services.screening_service as screening_service
    monkeypatch.setattr(clients, "supabase_client", db)
    monkeypatch.setattr(screening_service, "supabase_client", db)
    # Quota gate is unit-tested separately; allow by default here.
    import services.billing_service as billing_service
    monkeypatch.setattr(billing_service, "check_quota", lambda uid, res: (True, None))
    return db


@pytest.fixture
def client(fake_db):
    from server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def auth_headers(**kw):
    return {"Authorization": f"Bearer {make_token(**kw)}"}


def seed_candidate(db, org_id=ORG_A, first="Testa", last="Candidate", phone="+353860000001"):
    row = {
        "id": str(uuid.uuid4()),
        "organization_id": org_id,
        "first_name": first,
        "last_name": last,
        "headline": "Synthetic QA Candidate",
        "location": "Dublin",
        "years_experience": 7,
        "industries": ["Technology"],
        "pipeline_stage": "sourced",
        "source_metadata": {"upload_phone": phone, "upload_email": "synthetic@example.test"},
    }
    db.store.setdefault("people_profiles", []).append(row)
    return row


# ── POST /api/v1/screens/phone ──────────────────────────────────────

class TestCreatePhoneScreen:
    def test_creates_job_and_linked_session(self, client, fake_db):
        cand = seed_candidate(fake_db)
        resp = client.post("/api/v1/screens/phone", json={
            "candidate_id": cand["id"],
            "phone": "+353860000001",
            "role_title": "Head of Engineering",
        }, headers=auth_headers())
        assert resp.status_code == 201, resp.get_json()
        data = resp.get_json()["data"]
        assert data["session_id"] and data["job_id"]
        assert data["status"] == "queued"

        # The proven call path: an outbound_call_jobs row queued for the dispatcher
        jobs = fake_db.store["outbound_call_jobs"]
        assert len(jobs) == 1
        job = jobs[0]
        assert job["status"] == "queued"
        assert job["phone_e164"] == "+353860000001"
        ctx_block = job["artifacts"]["screening_context"]
        assert ctx_block["candidate_name"] == "Testa Candidate"
        assert ctx_block["source_candidate_id"] == cand["id"]

        # The bridge: an org-scoped screening session linked to the job
        sessions = fake_db.store["screening_sessions"]
        assert len(sessions) == 1
        session = sessions[0]
        assert session["organization_id"] == ORG_A
        assert session["candidate_id"] == cand["id"]
        assert session["state"] == "in_progress"
        assert session["metadata"]["outbound_call_job_id"] == job["id"]
        assert session["metadata"]["channel"] == "aidan_phone"
        assert len(session["questions"]) == 3  # default question set

    def test_cross_org_candidate_is_404(self, client, fake_db):
        cand = seed_candidate(fake_db, org_id=ORG_B)
        resp = client.post("/api/v1/screens/phone", json={
            "candidate_id": cand["id"], "phone": "+353860000001",
        }, headers=auth_headers(org_id=ORG_A))
        assert resp.status_code == 404
        assert "outbound_call_jobs" not in fake_db.store

    def test_requires_auth(self, client):
        resp = client.post("/api/v1/screens/phone", json={"candidate_id": "x", "phone": "+353860000001"})
        assert resp.status_code == 401

    def test_viewer_role_forbidden(self, client, fake_db):
        cand = seed_candidate(fake_db)
        resp = client.post("/api/v1/screens/phone", json={
            "candidate_id": cand["id"], "phone": "+353860000001",
        }, headers=auth_headers(role="viewer"))
        assert resp.status_code == 403

    def test_invalid_phone_rejected(self, client, fake_db):
        cand = seed_candidate(fake_db)
        resp = client.post("/api/v1/screens/phone", json={
            "candidate_id": cand["id"], "phone": "not-a-number",
        }, headers=auth_headers())
        assert resp.status_code == 400

    def test_quota_exceeded_maps_to_403(self, client, fake_db, monkeypatch):
        import services.billing_service as billing_service
        monkeypatch.setattr(billing_service, "check_quota",
                            lambda uid, res: (False, "Free tier limit reached"))
        cand = seed_candidate(fake_db)
        resp = client.post("/api/v1/screens/phone", json={
            "candidate_id": cand["id"], "phone": "+353860000001",
        }, headers=auth_headers())
        assert resp.status_code == 403
        assert "Free tier" in resp.get_json()["error"]


# ── Call status + read-through sync ─────────────────────────────────

def start_screen(client, fake_db):
    cand = seed_candidate(fake_db)
    resp = client.post("/api/v1/screens/phone", json={
        "candidate_id": cand["id"], "phone": "+353860000001",
        "role_title": "Head of Engineering",
    }, headers=auth_headers())
    assert resp.status_code == 201
    return cand, resp.get_json()["data"]


def complete_call(fake_db, job_id):
    """Simulate what the dispatcher/voice pipeline writes on completion."""
    job = next(j for j in fake_db.store["outbound_call_jobs"] if j["id"] == job_id)
    job["status"] = "succeeded"
    job["artifacts"]["call_status"] = "completed"
    ix = next(i for i in fake_db.store["interactions"] if i["id"] == job["interaction_id"])
    ix["transcript_text"] = "AI Dan: Hello... Candidate: (synthetic transcript)"
    ix["screening_scores"] = [
        {"question": "Q1", "competency": "Experience", "weight": 1.0,
         "response_summary": "Strong current-role account", "score": 4},
        {"question": "Q2", "competency": "Motivation", "weight": 1.0,
         "response_summary": "Clear growth motivation", "score": 5},
        {"question": "Q3", "competency": "Impact", "weight": 1.0,
         "response_summary": "Quantified achievement", "score": 3},
    ]
    ix["screening_recommendation"] = "proceed"
    ix["artifacts"] = {"candidate_extraction": {"summary": "Synthetic strong candidate"}}


class TestCallStatusAndSync:
    def test_queued_status_passthrough(self, client, fake_db):
        _, data = start_screen(client, fake_db)
        resp = client.get(f"/api/v1/screens/{data['session_id']}/call-status", headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["status"] == "queued"
        assert body["session_id"] == data["session_id"]
        # session untouched while the call is pending
        assert fake_db.store["screening_sessions"][0]["state"] == "in_progress"

    def test_completed_call_syncs_into_session(self, client, fake_db):
        cand, data = start_screen(client, fake_db)
        complete_call(fake_db, data["job_id"])

        resp = client.get(f"/api/v1/screens/{data['session_id']}/call-status", headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["status"] == "completed"
        assert body["recommendation"] == "proceed"
        assert len(body["scores"]) == 3

        session = fake_db.store["screening_sessions"][0]
        assert session["state"] == "complete"
        assert session["completed_at"]
        # answers carry both console keys and state-machine keys, scores ×2
        assert session["answers"][0]["question_index"] == 0
        assert session["answers"][0]["text"] == "Strong current-role account"
        assert session["answers"][0]["response_text"] == session["answers"][0]["text"]
        assert [a["score"] for a in session["answers"]] == [8, 10, 6]
        assert session["outcome"]["recommendation"] == "proceed"
        assert session["outcome"]["score"] == 0.8  # avg 4/5
        assert session["metadata"]["transcript"].startswith("AI Dan:")

        # compliance decision logged for the review queue
        decisions = fake_db.store["ai_decision_log"]
        assert decisions[0]["decision_type"] == "screening_score"
        assert decisions[0]["candidate_id"] == cand["id"]
        assert decisions[0]["organization_id"] == ORG_A

    def test_get_screen_read_through_sync(self, client, fake_db):
        _, data = start_screen(client, fake_db)
        complete_call(fake_db, data["job_id"])
        resp = client.get(f"/api/v1/screens/{data['session_id']}", headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["state"] == "complete"
        assert body["outcome"]["recommendation"] == "proceed"

    def test_list_screens_by_candidate(self, client, fake_db):
        cand, data = start_screen(client, fake_db)
        complete_call(fake_db, data["job_id"])
        resp = client.get(f"/api/v1/screens?candidate_id={cand['id']}", headers=auth_headers())
        assert resp.status_code == 200
        sessions = resp.get_json()["data"]["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["state"] == "complete"

    def test_cross_org_session_is_404(self, client, fake_db):
        _, data = start_screen(client, fake_db)
        resp = client.get(f"/api/v1/screens/{data['session_id']}/call-status",
                          headers=auth_headers(org_id=ORG_B))
        assert resp.status_code == 404

    def test_failed_call_moves_session_to_handoff(self, client, fake_db):
        _, data = start_screen(client, fake_db)
        job = fake_db.store["outbound_call_jobs"][0]
        job["artifacts"]["call_status"] = "no-answer"
        resp = client.get(f"/api/v1/screens/{data['session_id']}/call-status", headers=auth_headers())
        assert resp.status_code == 200
        assert resp.get_json()["data"]["status"] == "no_answer"
        session = fake_db.store["screening_sessions"][0]
        assert session["state"] == "handoff"
        assert session["handoff_reason"] == "call_no_answer"


# ── Console contract regressions ────────────────────────────────────

class TestConsoleContracts:
    def test_pipeline_board_returns_stages_array(self, client, fake_db):
        seed_candidate(fake_db)
        resp = client.get("/api/v1/pipeline", headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert "stages" in body
        stages = {s["stage"]: s for s in body["stages"]}
        assert stages["sourced"]["count"] == 1
        assert stages["sourced"]["candidates"][0]["full_name"] == "Testa Candidate"

    def test_decision_family_filter_prefix_matches(self, client, fake_db):
        fake_db.store.setdefault("ai_decision_log", []).append({
            "id": str(uuid.uuid4()),
            "organization_id": ORG_A,
            "decision_type": "screening_score",
            "candidate_id": "c1",
            "human_reviewed": False,
        })
        resp = client.get("/api/v1/compliance/decisions?type=screening", headers=auth_headers())
        assert resp.status_code == 200
        assert len(resp.get_json()["data"]["decisions"]) == 1


# ── Candidates serializer ───────────────────────────────────────────

class TestCandidateSerializer:
    def test_get_returns_console_shape(self, client, fake_db):
        cand = seed_candidate(fake_db)
        resp = client.get(f"/api/v1/candidates/{cand['id']}", headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["full_name"] == "Testa Candidate"
        assert body["phone"] == "+353860000001"
        assert body["email"] == "synthetic@example.test"
        assert body["experience_years"] == 7
        assert body["industry"] == "Technology"
        assert isinstance(body["skills"], list)

    def test_create_accepts_console_fields(self, client, fake_db):
        resp = client.post("/api/v1/candidates", json={
            "full_name": "Synthetic Person",
            "email": "sp@example.test",
            "phone": "+353860000002",
            "location": "Cork",
            "experience_years": 4,
            "industry": "Finance",
        }, headers=auth_headers())
        assert resp.status_code == 201, resp.get_json()
        body = resp.get_json()["data"]
        assert body["full_name"] == "Synthetic Person"
        assert body["phone"] == "+353860000002"
        assert body["email"] == "sp@example.test"
        row = fake_db.store["people_profiles"][0]
        assert row["first_name"] == "Synthetic"
        assert row["last_name"] == "Person"
        assert row["source_metadata"]["upload_phone"] == "+353860000002"

    def test_update_merges_contact_into_source_metadata(self, client, fake_db):
        cand = seed_candidate(fake_db)
        resp = client.patch(f"/api/v1/candidates/{cand['id']}", json={
            "phone": "+353860000009",
        }, headers=auth_headers())
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        assert body["phone"] == "+353860000009"
        # existing metadata preserved
        assert body["source_metadata"]["upload_email"] == "synthetic@example.test"
