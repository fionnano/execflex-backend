"""
Regression tests for the five Critical findings of the 2026-10-16 security
audit (AUDIT_2026-10-16.md, section 0; workpaper E_execflex.md):

  SEC-E-001  POST /voice-session/cara took a spoofable Origin header as auth and
             the WebSocket bridge had no auth at all.
  SEC-E-002  Unauthenticated /voice/debug/* routes served per-call transcript
             logs from /tmp.
  SEC-E-003  /screening/bias-audit/<role_id> interpolated the path into a
             PostgREST or= filter and the table had no organisation column.
  SEC-E-004  /screening/<job_id>/status and /retry-extraction had no ownership
             check.
  SEC-E-005  Legacy POST /match dumped approved people_profiles across tenants.

Real route handlers and the real auth decorators run; only the Supabase client
is replaced with an in-memory fake. All data is synthetic.
"""
import json
import os
import sys
import time
import uuid
from urllib.parse import parse_qs, quote, urlparse

import pytest
import jwt as pyjwt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROUTES_DIR = os.path.join(os.path.dirname(__file__), "..", "routes")

JWT_SECRET = "audit-test-jwt-secret"
SERVICE_KEY = "audit-test-service-key"
WS_SIGNING_SECRET = "audit-test-ws-signing-secret"

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
USER_A = str(uuid.uuid4())
USER_B = str(uuid.uuid4())
USER_NO_ORG = str(uuid.uuid4())

SERVICE_HEADERS = {"X-Service-Key": SERVICE_KEY}
PROMPT = "You are a synthetic test assistant."


# ── In-memory fake Supabase ─────────────────────────────────────────────────

class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, store, table_name):
        self.store = store
        self.table_name = table_name
        self.filters = []
        self._count = None
        self._order = None
        self._range = None
        self._limit = None
        self._insert_rows = None
        self._update_values = None

    def select(self, *cols, count=None):
        self._count = count
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def or_(self, expr):
        raise AssertionError(
            f"PostgREST or= filter built from request input (SEC-E-003): {expr!r}"
        )

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def limit(self, n):
        self._limit = n
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
            if op == "is" and row.get(col) is not val:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        if self._insert_rows is not None:
            inserted = []
            for r in self._insert_rows:
                r = dict(r)
                r.setdefault("id", str(uuid.uuid4()))
                r.setdefault("created_at", "2026-10-16T00:00:00+00:00")
                rows.append(r)
                inserted.append(dict(r))
            return FakeResult(inserted)
        if self._update_values is not None:
            updated = []
            for r in rows:
                if self._matches(r):
                    r.update(self._update_values)
                    updated.append(dict(r))
            return FakeResult(updated)
        matched = [dict(r) for r in rows if self._matches(r)]
        total = len(matched)
        if self._order:
            col, desc = self._order
            matched.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._range:
            start, end = self._range
            matched = matched[start:end + 1]
        elif self._limit is not None:
            matched = matched[:self._limit]
        return FakeResult(matched, count=total if self._count else None)


class FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeQuery(self.store, name)


class FakeWS:
    """Stand-in for the simple_websocket Server the handler talks to."""

    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(json.loads(data))

    def receive(self, timeout=None):
        return None

    def close(self, *args, **kwargs):
        pass


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def env(monkeypatch):
    import config.app_config as app_config
    import routes.cara_voice as cara_voice

    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setattr(app_config, "SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("AINM_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("CARA_WS_SIGNING_SECRET", WS_SIGNING_SECRET)
    monkeypatch.delenv("SMOKE_TEST_BYPASS_SECRET", raising=False)
    for name in ("CARA_SESSION_LIMIT_PER_USER", "CARA_SESSION_LIMIT_PER_IP",
                 "CARA_SESSION_LIMIT_PER_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cara_voice, "_quota_hits", {})


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeSupabase()
    import config.clients as clients
    import services.screening_service as screening_service
    monkeypatch.setattr(clients, "supabase_client", db)
    monkeypatch.setattr(screening_service, "supabase_client", db)
    return db


@pytest.fixture
def client(env, fake_db):
    from server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def token(user_id=USER_A, org_id=ORG_A, role="recruiter"):
    payload = {"sub": user_id, "aud": "authenticated", "exp": int(time.time()) + 600}
    if org_id:
        payload["app_metadata"] = {"org_id": org_id, "role": role}
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")


def bearer(**kw):
    return {"Authorization": f"Bearer {token(**kw)}"}


def create_cara_session(client, headers):
    resp = client.post("/voice-session/cara", json={"system_prompt": PROMPT}, headers=headers)
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    parsed = urlparse(body["ws_url"])
    ws_token = parse_qs(parsed.query).get("token", [None])[0]
    return body["session_id"], ws_token, parsed


def seed_audit_row(db, org_id, role_id, **extra):
    row = {
        "id": str(uuid.uuid4()),
        "interaction_id": str(uuid.uuid4()),
        "job_id": str(uuid.uuid4()),
        "organization_id": org_id,
        "role_id": role_id,
        "role_title": "Synthetic Role",
        "company_name": "Synthetic Co",
        "questions_asked": 3,
        "questions_expected": 3,
        "question_order_preserved": True,
        "overall_score": 3.5,
        "recommendation": "proceed",
        "bias_flags": [],
        "ai_disclosure_given": True,
        "candidate_consented": True,
        "created_at": "2026-10-01T00:00:00+00:00",
    }
    row.update(extra)
    db.store.setdefault("screening_bias_audit", []).append(row)
    return row


def seed_job(db, org_id=None, user_id=None, role_id=None):
    job = {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "user_id": user_id,
        "interaction_id": str(uuid.uuid4()),
        "artifacts": {
            "call_type": "screening",
            "screening_context": {"role_id": role_id, "candidate_name": "Synthetic Candidate"},
        },
    }
    if org_id:
        job["artifacts"]["organization_id"] = org_id
    db.store.setdefault("outbound_call_jobs", []).append(job)
    return job


# ── SEC-E-001: Cara voice session auth, signed ws_url, quotas ───────────────

class TestSecE001CaraSessionAuth:
    def test_origin_header_alone_is_not_authentication(self, client):
        for origin in ("https://ainm.ai", "https://www.ainm.ai", "https://execflex.ai",
                       "http://localhost:5173", "http://localhost:3000"):
            resp = client.post("/voice-session/cara", json={"system_prompt": PROMPT},
                               headers={"Origin": origin})
            assert resp.status_code == 401, origin
            assert "session_id" not in (resp.get_json() or {})

    def test_no_credentials_is_401(self, client):
        resp = client.post("/voice-session/cara", json={"system_prompt": PROMPT})
        assert resp.status_code == 401

    def test_wrong_service_key_is_401(self, client):
        resp = client.post("/voice-session/cara", json={"system_prompt": PROMPT},
                           headers={"X-Service-Key": "not-the-key", "Origin": "https://ainm.ai"})
        assert resp.status_code == 401

    def test_service_key_gets_signed_session_bound_ws_url(self, client):
        from routes.cara_voice import get_session_prompt, verify_ws_token

        session_id, ws_token, parsed = create_cara_session(client, SERVICE_HEADERS)
        assert parsed.scheme in ("ws", "wss")
        assert parsed.path == f"/voice/cara/ws/{session_id}"
        assert ws_token

        assert verify_ws_token(session_id, ws_token)
        # Bound to the session id: the same token opens no other session.
        assert not verify_ws_token(str(uuid.uuid4()), ws_token)
        expires, signature = ws_token.split(".")
        assert not verify_ws_token(session_id, f"{expires}.{'0' * len(signature)}")
        assert not verify_ws_token(session_id, f"{int(expires) + 60}.{signature}")
        # Short-lived: dead once the expiry has passed.
        assert not verify_ws_token(session_id, ws_token, now=int(expires) + 1)
        assert not verify_ws_token(session_id, "")
        assert not verify_ws_token(session_id, None)

        # Clean up the on-disk session.
        assert get_session_prompt(session_id) == PROMPT

    def test_jwt_user_can_create_session(self, client):
        from routes.cara_voice import get_session_prompt
        session_id, ws_token, _ = create_cara_session(client, bearer())
        assert ws_token
        assert get_session_prompt(session_id) == PROMPT

    def test_missing_prompt_is_400(self, client):
        resp = client.post("/voice-session/cara", json={}, headers=SERVICE_HEADERS)
        assert resp.status_code == 400

    def test_ws_handler_rejects_missing_or_bad_token(self, client):
        from server import app
        from routes.cara_voice import get_session_prompt
        from routes.cara_websocket import cara_ws_handler

        session_id, _, _ = create_cara_session(client, SERVICE_HEADERS)
        far_future = int(time.time()) + 3600
        for query in ({}, {"token": "garbage"}, {"token": f"{far_future}.{'0' * 64}"},
                      {"token": f"{far_future}.{'é' * 64}"}, {"token": f"{far_future}."}):
            ws = FakeWS()
            with app.test_request_context(f"/voice/cara/ws/{session_id}", query_string=query):
                cara_ws_handler(ws, session_id)
            assert ws.sent == [{"type": "error", "message": "Unauthorized"}], query

        # The session was not consumed by the rejected attempts.
        assert get_session_prompt(session_id) == PROMPT

    def test_ws_handler_accepts_signed_token(self, client, monkeypatch):
        from server import app
        import routes.cara_websocket as cara_websocket
        from routes.cara_voice import get_session_prompt

        # Stop the handler right after auth, before any outbound connection.
        monkeypatch.setattr(cara_websocket, "OPENAI_API_KEY", "")

        session_id, ws_token, _ = create_cara_session(client, SERVICE_HEADERS)
        ws = FakeWS()
        with app.test_request_context(f"/voice/cara/ws/{session_id}", query_string={"token": ws_token}):
            cara_websocket.cara_ws_handler(ws, session_id)

        assert ws.sent == [{"type": "error", "message": "OpenAI not configured on server"}]
        # Auth passed and the single-use session was consumed.
        assert get_session_prompt(session_id) is None

    def test_per_user_session_quota(self, client, monkeypatch):
        monkeypatch.setenv("CARA_SESSION_LIMIT_PER_USER", "2")
        for _ in range(2):
            assert client.post("/voice-session/cara", json={"system_prompt": PROMPT},
                               headers=bearer()).status_code == 201
        resp = client.post("/voice-session/cara", json={"system_prompt": PROMPT}, headers=bearer())
        assert resp.status_code == 429
        # Another user has their own quota.
        assert client.post("/voice-session/cara", json={"system_prompt": PROMPT},
                           headers=bearer(user_id=USER_B, org_id=ORG_B)).status_code == 201

    def test_per_ip_session_quota(self, client, monkeypatch):
        monkeypatch.setenv("CARA_SESSION_LIMIT_PER_USER", "100")
        monkeypatch.setenv("CARA_SESSION_LIMIT_PER_IP", "1")
        assert client.post("/voice-session/cara", json={"system_prompt": PROMPT},
                           headers=bearer()).status_code == 201
        resp = client.post("/voice-session/cara", json={"system_prompt": PROMPT},
                           headers=bearer(user_id=USER_B, org_id=ORG_B))
        assert resp.status_code == 429

    def test_service_identity_quota(self, client, monkeypatch):
        monkeypatch.setenv("CARA_SESSION_LIMIT_PER_SERVICE", "1")
        assert client.post("/voice-session/cara", json={"system_prompt": PROMPT},
                           headers=SERVICE_HEADERS).status_code == 201
        resp = client.post("/voice-session/cara", json={"system_prompt": PROMPT},
                           headers=SERVICE_HEADERS)
        assert resp.status_code == 429

    def test_signing_key_fallback_is_stable_without_env(self, monkeypatch, tmp_path):
        import routes.cara_voice as cara_voice
        monkeypatch.delenv("CARA_WS_SIGNING_SECRET", raising=False)
        monkeypatch.setattr(cara_voice, "_WS_SIGNING_KEY_FILE", str(tmp_path / "signing_key"))

        first = cara_voice._ws_signing_key()
        second = cara_voice._ws_signing_key()
        assert first == second
        assert len(first) >= 32
        session_id = str(uuid.uuid4())
        assert cara_voice.verify_ws_token(session_id, cara_voice.make_ws_token(session_id))

    def test_no_origin_allow_list_remains_in_session_route(self):
        with open(os.path.join(ROUTES_DIR, "cara_voice.py"), encoding="utf-8") as f:
            source = f.read()
        assert "_CARA_ALLOWED_ORIGINS" not in source
        assert 'headers.get("Origin") or "").rstrip' not in source


# ── SEC-E-002: debug log routes and on-disk transcript logs ─────────────────

class TestSecE002DebugLogRoutesRemoved:
    def test_debug_routes_are_gone(self, client):
        from server import app
        assert client.get("/voice/debug/latest-log").status_code == 404
        assert client.get("/voice/debug/handler-log/CA00000000000000000000000000000000").status_code == 404
        assert client.get("/voice/debug/handler-log/CA00000000000000000000000000000000",
                          headers=SERVICE_HEADERS).status_code == 404
        debug_rules = [r.rule for r in app.url_map.iter_rules() if "debug" in r.rule.lower()]
        assert debug_rules == []

    def test_no_per_call_transcript_log_files_are_written(self):
        for fname in ("voice_websocket.py", "voice.py"):
            with open(os.path.join(ROUTES_DIR, fname), encoding="utf-8") as f:
                source = f.read()
            assert "openai_handler_{" not in source, f"{fname} still names the per-call log file"
            assert 'log_dir = "/tmp"' not in source, f"{fname} still points a log directory at /tmp"
            assert "open(log_file" not in source, f"{fname} still opens a per-call log file"


# ── SEC-E-003: bias audit is UUID-only, parameterised and org-scoped ────────

class TestSecE003BiasAuditScoped:
    def test_non_uuid_role_id_is_rejected_before_any_query(self, client, fake_db):
        role_id = str(uuid.uuid4())
        seed_audit_row(fake_db, ORG_A, role_id, role_title="Chief Financial Officer")
        for bad in ("Chief Financial Officer", "x,role_id.not.is.null",
                    "00000000-0000-0000-0000-00000000000Z", "1"):
            resp = client.get(f"/screening/bias-audit/{quote(bad, safe='')}", headers=bearer())
            assert resp.status_code == 400, bad

    def test_returns_only_callers_org_rows_for_that_role(self, client, fake_db):
        role_id = str(uuid.uuid4())
        other_role = str(uuid.uuid4())
        seed_audit_row(fake_db, ORG_A, role_id, overall_score=4.0)
        seed_audit_row(fake_db, ORG_A, role_id, overall_score=3.0)
        seed_audit_row(fake_db, ORG_B, role_id, overall_score=1.0)      # another tenant
        seed_audit_row(fake_db, ORG_A, other_role)                       # another role
        seed_audit_row(fake_db, None, role_id)                           # unattributed legacy row

        resp = client.get(f"/screening/bias-audit/{role_id}", headers=bearer(org_id=ORG_A))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["total_screenings"] == 2
        assert len(body["records"]) == 2
        assert {r["organization_id"] for r in body["records"]} == {ORG_A}
        assert {r["role_id"] for r in body["records"]} == {role_id}
        assert body["summary"]["average_score"] == 3.5

        resp_b = client.get(f"/screening/bias-audit/{role_id}", headers=bearer(user_id=USER_B, org_id=ORG_B))
        assert resp_b.status_code == 200
        assert resp_b.get_json()["total_screenings"] == 1
        assert resp_b.get_json()["records"][0]["organization_id"] == ORG_B

    def test_uppercase_uuid_is_canonicalised(self, client, fake_db):
        role_id = str(uuid.uuid4())
        seed_audit_row(fake_db, ORG_A, role_id)
        resp = client.get(f"/screening/bias-audit/{role_id.upper()}", headers=bearer())
        assert resp.status_code == 200
        assert resp.get_json()["total_screenings"] == 1

    def test_caller_without_organisation_gets_403(self, client, fake_db):
        role_id = str(uuid.uuid4())
        seed_audit_row(fake_db, ORG_A, role_id)
        assert client.get(f"/screening/bias-audit/{role_id}",
                          headers=bearer(user_id=USER_NO_ORG, org_id=None)).status_code == 403
        assert client.get(f"/screening/bias-audit/{role_id}", headers=SERVICE_HEADERS).status_code == 403
        assert client.get(f"/screening/bias-audit/{role_id}").status_code == 401

    def test_org_resolved_from_user_roles_when_jwt_lacks_claim(self, client, fake_db):
        role_id = str(uuid.uuid4())
        seed_audit_row(fake_db, ORG_A, role_id)
        seed_audit_row(fake_db, ORG_B, role_id)
        fake_db.store["user_roles"] = [
            {"user_id": USER_NO_ORG, "organization_id": ORG_A, "created_at": "2026-01-01T00:00:00+00:00"},
        ]
        resp = client.get(f"/screening/bias-audit/{role_id}", headers=bearer(user_id=USER_NO_ORG, org_id=None))
        assert resp.status_code == 200
        assert resp.get_json()["total_screenings"] == 1
        assert resp.get_json()["records"][0]["organization_id"] == ORG_A

    def test_new_audit_rows_are_stamped_with_organisation(self, fake_db):
        from services.screening_service import _log_bias_audit
        _log_bias_audit(
            interaction_id=str(uuid.uuid4()),
            job_id=str(uuid.uuid4()),
            role_id=str(uuid.uuid4()),
            role_title="Synthetic Role",
            company_name="Synthetic Co",
            questions=[{"question": "Tell me about your current role."}],
            scores=[{"question": "Tell me about your current role.", "score": 3}],
            overall_score=3.0,
            recommendation="hold",
            bias_flags=[],
            transcript="Assistant: I am an AI screening assistant. Candidate: okay.",
            call_duration_seconds=42,
            organization_id=ORG_A,
        )
        rows = fake_db.store["screening_bias_audit"]
        assert len(rows) == 1
        assert rows[0]["organization_id"] == ORG_A

    def test_screening_routes_build_no_interpolated_filters(self):
        with open(os.path.join(ROUTES_DIR, "screening.py"), encoding="utf-8") as f:
            source = f.read()
        assert ".or_(f" not in source
        assert ".or_(" not in source


# ── SEC-E-004: screening job ownership ──────────────────────────────────────

class TestSecE004JobOwnership:
    def test_other_orgs_job_status_is_404(self, client, fake_db):
        job = seed_job(fake_db, org_id=ORG_B, user_id=USER_B)
        resp = client.get(f"/screening/{job['id']}/status", headers=bearer(user_id=USER_A, org_id=ORG_A))
        assert resp.status_code == 404
        assert "transcript" not in (resp.get_json() or {})

    def test_other_orgs_retry_is_404_and_queues_nothing(self, client, fake_db, monkeypatch):
        import services.call_extraction_service as extraction
        calls = []
        monkeypatch.setattr(extraction, "extract_candidate_profile_async", lambda *a, **k: calls.append(a))
        monkeypatch.setattr(extraction, "extract_employer_brief_async", lambda *a, **k: calls.append(a))

        job = seed_job(fake_db, org_id=ORG_B, user_id=USER_B)
        resp = client.post(f"/screening/{job['id']}/retry-extraction", headers=bearer(user_id=USER_A, org_id=ORG_A))
        assert resp.status_code == 404
        assert calls == []

    def test_own_org_job_is_visible_to_colleague(self, client, fake_db):
        job = seed_job(fake_db, org_id=ORG_A, user_id=USER_B)   # created by a colleague
        resp = client.get(f"/screening/{job['id']}/status", headers=bearer(user_id=USER_A, org_id=ORG_A))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["job_id"] == job["id"]
        assert body["status"] == "queued"

    def test_owner_can_retry_extraction(self, client, fake_db, monkeypatch):
        import services.call_extraction_service as extraction
        calls = []
        monkeypatch.setattr(extraction, "extract_candidate_profile_async", lambda *a, **k: calls.append(a))

        job = seed_job(fake_db, org_id=ORG_A, user_id=USER_A)
        resp = client.post(f"/screening/{job['id']}/retry-extraction", headers=bearer())
        assert resp.status_code == 200
        assert calls == [(job["interaction_id"], job["id"])]

    def test_creator_sees_legacy_job_without_org_stamp(self, client, fake_db):
        job = seed_job(fake_db, user_id=USER_A)   # pre-fix row: no organisation anywhere
        assert client.get(f"/screening/{job['id']}/status", headers=bearer()).status_code == 200
        assert client.get(f"/screening/{job['id']}/status",
                          headers=bearer(user_id=USER_B, org_id=ORG_B)).status_code == 404

    def test_legacy_job_org_resolved_via_opportunity(self, client, fake_db):
        opportunity_id = str(uuid.uuid4())
        fake_db.store["opportunities"] = [{"id": opportunity_id, "organization_id": ORG_A}]
        job = seed_job(fake_db, role_id=opportunity_id)   # no org stamp, no creator
        assert client.get(f"/screening/{job['id']}/status",
                          headers=bearer(user_id=USER_B, org_id=ORG_A)).status_code == 200
        assert client.get(f"/screening/{job['id']}/status",
                          headers=bearer(user_id=USER_B, org_id=ORG_B)).status_code == 404

    def test_unknown_and_malformed_ids_are_404(self, client, fake_db):
        assert client.get(f"/screening/{uuid.uuid4()}/status", headers=bearer()).status_code == 404
        assert client.get("/screening/not-a-uuid/status", headers=bearer()).status_code == 404
        assert client.post("/screening/not-a-uuid/retry-extraction", headers=bearer()).status_code == 404

    def test_unauthenticated_is_401(self, client, fake_db):
        job = seed_job(fake_db, org_id=ORG_A, user_id=USER_A)
        assert client.get(f"/screening/{job['id']}/status").status_code == 401

    def test_service_identity_reads_jobs_it_created(self, client, fake_db):
        job = seed_job(fake_db)   # service-created: no user, no ExecFlex organisation
        resp = client.get(f"/screening/{job['id']}/status", headers=SERVICE_HEADERS)
        assert resp.status_code == 200
        assert resp.get_json()["job_id"] == job["id"]

    def test_jobs_are_stamped_with_organisation_on_creation(self, fake_db):
        from services.screening_service import create_screening_job
        result = create_screening_job(
            candidate_phone="+353860000001",
            candidate_name="Synthetic Candidate",
            role_title="Synthetic Role",
            company_name="Synthetic Co",
            questions=[{"question": "q", "competency": "c", "weight": 1.0}],
            callback_url=None,
            source_candidate_id=None,
            user_id=USER_A,
            role_id=str(uuid.uuid4()),
            organization_id=ORG_A,
        )
        job = fake_db.store["outbound_call_jobs"][0]
        assert job["id"] == result["job_id"]
        assert job["artifacts"]["organization_id"] == ORG_A

    def test_job_org_resolution_order(self, fake_db):
        from utils.org_scope import resolve_job_org_id
        assert resolve_job_org_id({"artifacts": {"organization_id": ORG_A}}) == ORG_A

        opportunity_id = str(uuid.uuid4())
        fake_db.store["opportunities"] = [{"id": opportunity_id, "organization_id": ORG_B}]
        assert resolve_job_org_id({"artifacts": {"screening_context": {"role_id": opportunity_id}}}) == ORG_B
        assert resolve_job_org_id({"artifacts": {"screening_context": {"role_id": "Not A Uuid"}}}) is None

        fake_db.store["user_roles"] = [{"user_id": USER_A, "organization_id": ORG_A}]
        assert resolve_job_org_id({"user_id": USER_A, "artifacts": {}}) == ORG_A
        assert resolve_job_org_id({"user_id": USER_B, "artifacts": {}}) is None
        assert resolve_job_org_id({"artifacts": None}) is None


# ── SEC-E-005: legacy POST /match removed ───────────────────────────────────

class TestSecE005MatchRouteRemoved:
    def test_match_route_is_gone(self, client):
        from server import app
        assert "matching" not in app.blueprints
        assert [r.rule for r in app.url_map.iter_rules() if r.rule == "/match"] == []
        assert client.post("/match", json={"industry": "Fintech"}, headers=bearer()).status_code == 404
        assert client.post("/match", json={"industry": "Fintech"}, headers=SERVICE_HEADERS).status_code == 404

    def test_matching_route_module_deleted(self):
        assert not os.path.exists(os.path.join(ROUTES_DIR, "matching.py"))

    def test_match_finder_module_kept_for_auto_match_service(self):
        import importlib
        match_finder = importlib.import_module("modules.match_finder")
        assert callable(match_finder.find_best_match)
        with open(os.path.join(os.path.dirname(__file__), "..", "services", "auto_match_service.py"),
                  encoding="utf-8") as f:
            assert "from modules.match_finder import find_best_match" in f.read()
