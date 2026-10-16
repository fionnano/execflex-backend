"""
Regression tests for the execflex High findings of the 2026-10-16 security
audit (AUDIT_2026-10-16.md section 0; workpapers E_execflex.md, V4_execflex.md):

  SEC-E-006  POST /screening/feedback-request enumerated screened phones and
             e-mailed scores to a caller-supplied address.
  SEC-E-007  create-shortlist had no ownership check; GET /shortlist/<id> was
             public and joined transcripts.
  SEC-E-008  JWTs decoded unverified when SUPABASE_JWT_SECRET was unset.
  SEC-E-009  Stripe webhook accepted unsigned payloads when the secret was unset.
  SEC-E-010  Any authenticated user listed sourced prospects for any opportunity.
  GDPR-E-001 Consent inferred from transcript keywords.
  GDPR-E-002 Data-rights "processing" only changed a status; deletion keyed on user_id.
  GDPR-E-003 PII, transcript previews and system prompts in logs.
  GDPR-E-004 Unsolicited sourcing/enrichment/outreach with no legal basis or suppression.
  GDPR-E-005 Transcripts and scores POSTed unsigned to any callback_url.

Real route handlers and the real auth decorators run; only the Supabase
client is replaced with an in-memory fake. All data is synthetic.
"""
import json
import os
import sys
import time
import uuid

import pytest
import jwt as pyjwt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = os.path.join(os.path.dirname(__file__), "..")
ROUTES_DIR = os.path.join(ROOT, "routes")

JWT_SECRET = "audit-high-test-jwt-secret"
SERVICE_KEY = "audit-high-test-service-key"

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
USER_A = str(uuid.uuid4())
USER_B = str(uuid.uuid4())
ADMIN = str(uuid.uuid4())

SERVICE_HEADERS = {"X-Service-Key": SERVICE_KEY}


# ── In-memory fake Supabase ─────────────────────────────────────────────────

class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def _json_path(row, col):
    """Resolve PostgREST JSON paths like source_metadata->>opportunity_id."""
    if "->" not in col:
        return row.get(col)
    parts = [p for p in col.replace("->>", "->").split("->") if p]
    value = row
    for part in parts:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


class FakeQuery:
    def __init__(self, store, table_name):
        self.store = store
        self.table_name = table_name
        self.filters = []
        self._order = None
        self._limit = None
        self._insert_rows = None
        self._update_values = None
        self._delete = False
        self._select = ""

    def select(self, *cols, count=None):
        self._select = " ".join(str(c) for c in cols)
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def filter(self, col, op, val):
        assert op == "eq"
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def order(self, col, desc=False, **_):
        self._order = (col, desc)
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

    def delete(self):
        self._delete = True
        return self

    def or_(self, expr):
        raise AssertionError(f"PostgREST or= filter built from request input: {expr!r}")

    def _matches(self, row):
        for op, col, val in self.filters:
            got = _json_path(row, col)
            if op == "eq" and got != val:
                return False
            if op == "in" and got not in val:
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
        if self._delete:
            kept, deleted = [], []
            for r in rows:
                (deleted if self._matches(r) else kept).append(r)
            self.store[self.table_name] = kept
            return FakeResult(deleted)
        matched = [dict(r) for r in rows if self._matches(r)]
        if "interaction:interactions(" in self._select:
            # Emulate the PostgREST embedded resource used by the shortlist page.
            by_id = {i["id"]: i for i in self.store.get("interactions", [])}
            for r in matched:
                r["interaction"] = by_id.get(r.get("interaction_id"))
        if self._order:
            col, desc = self._order
            matched.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._limit is not None:
            matched = matched[:self._limit]
        return FakeResult(matched)


class FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeQuery(self.store, name)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def env(monkeypatch):
    import config.app_config as app_config
    import routes.screening as screening_routes
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setattr(app_config, "SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("AINM_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.delenv("SMOKE_TEST_BYPASS_SECRET", raising=False)
    monkeypatch.delenv("SHORTLIST_SHARE_SECRET", raising=False)
    monkeypatch.delenv("CALLBACK_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("SOURCING_LEGAL_BASIS", raising=False)
    monkeypatch.delenv("SOURCING_LIA_REF", raising=False)
    monkeypatch.setattr(screening_routes, "_feedback_requests_seen", {})


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeSupabase()
    import config.clients as clients
    import services.screening_service as screening_service
    import services.voice_call_service as voice_call_service
    import services.auto_match_service as auto_match_service
    import routes.shortlist as shortlist_routes
    import routes.roles as roles_routes
    import routes.billing as billing_routes
    import routes.screening as screening_routes
    import routes.introductions as introductions_routes
    import routes.onboarding as onboarding_routes
    import utils.auth_helpers as auth_helpers
    for mod in (clients, auth_helpers, onboarding_routes, screening_service, voice_call_service, auto_match_service,
                shortlist_routes, roles_routes, billing_routes, introductions_routes):
        monkeypatch.setattr(mod, "supabase_client", db)
    if hasattr(screening_routes, "supabase_client"):
        monkeypatch.setattr(screening_routes, "supabase_client", db)
    return db


@pytest.fixture
def client(env, fake_db):
    from server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def token(user_id=USER_A, org_id=ORG_A, role="recruiter", aud="authenticated", secret=JWT_SECRET):
    payload = {"sub": user_id, "exp": int(time.time()) + 600}
    if aud:
        payload["aud"] = aud
    if org_id:
        payload["app_metadata"] = {"org_id": org_id, "role": role}
    return pyjwt.encode(payload, secret, algorithm="HS256")


def bearer(**kw):
    return {"Authorization": f"Bearer {token(**kw)}"}


def seed_opportunity(db, org_id=ORG_A, created_by=USER_A, title="Synthetic Role"):
    opp = {"id": str(uuid.uuid4()), "organization_id": org_id, "created_by_user_id": created_by,
           "title": title, "company_name": "Synthetic Co"}
    db.store.setdefault("opportunities", []).append(opp)
    return opp


def seed_profile(db, opportunity_id=None, **extra):
    row = {
        "id": str(uuid.uuid4()), "first_name": "Synthetic", "last_name": "Prospect",
        "headline": "CFO at Example", "location": "Dublin", "years_experience": 12,
        "approved": False, "source": "apollo",
        "source_metadata": {
            "apollo_id": "pdl-1", "opportunity_id": opportunity_id, "provider": "pdl",
            "has_email": True, "has_phone": "Yes", "linkedin_url": "https://linkedin.com/in/synthetic",
            "enriched_email": "prospect@example.com", "enriched_phone": "+353860000111",
        },
    }
    row.update(extra)
    db.store.setdefault("people_profiles", []).append(row)
    return row


def seed_screening_job(db, phone="+353861111111", candidate_email=None, interaction=True,
                       user_id=None, role_id=None, **ctx_extra):
    interaction_id = str(uuid.uuid4()) if interaction else None
    if interaction_id:
        db.store.setdefault("interactions", []).append({
            "id": interaction_id, "transcript_text": "Assistant: hi\nUser: hello",
            "screening_scores": [{"question": "Q1", "competency": "C", "score": 4}],
            "screening_recommendation": "proceed", "summary_text": "fine",
            "candidate_token": str(uuid.uuid4()),
        })
        db.store.setdefault("interaction_turns", []).append(
            {"id": str(uuid.uuid4()), "interaction_id": interaction_id, "speaker": "user", "text": "hello"})
    ctx = {"candidate_name": "Vic Tim", "role_title": "Engineer", "company_name": "Co",
           "candidate_email": candidate_email, "role_id": role_id, "questions": [{"question": "Q1"}]}
    ctx.update(ctx_extra)
    job = {"id": str(uuid.uuid4()), "interaction_id": interaction_id, "phone_e164": phone,
           "user_id": user_id, "status": "succeeded",
           "artifacts": {"call_type": "screening", "screening_context": ctx, "call_duration": 300}}
    db.store.setdefault("outbound_call_jobs", []).append(job)
    return job


# ── SEC-E-008: fail-closed JWT verification ─────────────────────────────────

class TestSecE008JwtFailClosed:
    def test_legacy_auth_rejects_every_token_without_secret(self, client, monkeypatch):
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        import config.app_config as app_config
        monkeypatch.setattr(app_config, "SUPABASE_JWT_SECRET", None)
        # Self-minted token with any secret, and an unsigned one.
        for tok in (token(secret="anything"), pyjwt.encode({"sub": USER_A}, "", algorithm="HS256")):
            resp = client.get("/screening/bias-audit/" + str(uuid.uuid4()),
                              headers={"Authorization": f"Bearer {tok}"})
            assert resp.status_code == 401
        # /api/v1 goes through the other decoder.
        resp = client.get("/api/v1/compliance/data-rights", headers=bearer(secret="anything"))
        assert resp.status_code == 401

    def test_wrong_secret_is_401_on_both_decoders(self, client):
        assert client.get("/screening/bias-audit/" + str(uuid.uuid4()),
                          headers=bearer(secret="not-the-secret")).status_code == 401
        assert client.get("/api/v1/compliance/data-rights",
                          headers=bearer(secret="not-the-secret")).status_code == 401

    def test_legacy_auth_now_checks_audience(self, client):
        assert client.get("/screening/bias-audit/" + str(uuid.uuid4()),
                          headers=bearer(aud="anon")).status_code == 401
        assert client.get("/screening/bias-audit/" + str(uuid.uuid4()),
                          headers=bearer(aud=None)).status_code == 401

    def test_missing_role_claim_defaults_to_viewer(self):
        from server import app
        from services.api.auth import extract_org_context
        tok = pyjwt.encode({"sub": USER_A, "aud": "authenticated", "app_metadata": {"org_id": ORG_A}},
                           JWT_SECRET, algorithm="HS256")
        import config.app_config as app_config
        app_config.SUPABASE_JWT_SECRET = JWT_SECRET
        with app.test_request_context("/", headers={"Authorization": f"Bearer {tok}"}):
            ctx = extract_org_context()
        assert ctx.role == "viewer"

    def test_no_unverified_decode_path_remains(self):
        for path in ("utils/auth_helpers.py", "services/api/auth.py"):
            with open(os.path.join(ROOT, path), encoding="utf-8") as f:
                assert "verify_signature" not in f.read(), path

    def test_production_startup_requires_secrets(self, monkeypatch):
        from config.app_config import validate_auth_config, is_production_env
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
        assert is_production_env()
        with pytest.raises(ValueError, match="SUPABASE_JWT_SECRET"):
            validate_auth_config()
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "s")
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        with pytest.raises(ValueError, match="STRIPE_WEBHOOK_SECRET"):
            validate_auth_config()
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
        assert validate_auth_config() is True
        # Render without APP_ENV is still production.
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv("RENDER", "true")
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        with pytest.raises(ValueError):
            validate_auth_config()
        # Local dev may run without them (bearer auth then always fails).
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.setenv("APP_ENV", "dev")
        assert validate_auth_config() is True


# ── SEC-E-009: Stripe webhook rejects unsigned payloads ─────────────────────

class TestSecE009StripeWebhook:
    FORGED = {"type": "checkout.session.completed",
              "data": {"object": {"customer": "cus_victim", "subscription": "sub_1", "metadata": {"tier": "growth"}}}}

    def test_no_secret_means_no_processing(self, client, fake_db, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        fake_db.store["organizations"] = [{"id": ORG_B, "stripe_customer_id": "cus_victim",
                                          "subscription_status": "inactive"}]
        resp = client.post("/billing/webhook", data=json.dumps(self.FORGED),
                           content_type="application/json")
        assert resp.status_code == 503
        assert fake_db.store["organizations"][0]["subscription_status"] == "inactive"

    def test_missing_or_bad_signature_is_rejected(self, client, fake_db, monkeypatch):
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
        fake_db.store["organizations"] = [{"id": ORG_B, "stripe_customer_id": "cus_victim",
                                          "subscription_status": "inactive"}]
        resp = client.post("/billing/webhook", data=json.dumps(self.FORGED),
                           content_type="application/json")
        assert resp.status_code == 400
        resp = client.post("/billing/webhook", data=json.dumps(self.FORGED),
                           content_type="application/json",
                           headers={"Stripe-Signature": "t=1,v1=deadbeef"})
        assert resp.status_code == 400
        assert fake_db.store["organizations"][0]["subscription_status"] == "inactive"

    def test_no_unsigned_parse_path_remains(self):
        with open(os.path.join(ROUTES_DIR, "billing.py"), encoding="utf-8") as f:
            source = f.read()
        assert "skipping signature verification" not in source
        assert 'type("Event"' not in source


# ── SEC-E-010: sourced candidates are owner-only and metadata is stripped ──

class TestSecE010SourcedCandidates:
    def test_other_tenant_gets_404(self, client, fake_db):
        opp = seed_opportunity(fake_db, org_id=ORG_B, created_by=USER_B)
        seed_profile(fake_db, opp["id"])
        resp = client.get(f"/roles/{opp['id']}/sourced-candidates", headers=bearer(user_id=USER_A, org_id=ORG_A))
        assert resp.status_code == 404
        assert "Prospect" not in resp.get_data(as_text=True)

    def test_owner_sees_rows_without_contact_values(self, client, fake_db):
        opp = seed_opportunity(fake_db, org_id=ORG_A, created_by=USER_B)  # colleague's role
        seed_profile(fake_db, opp["id"])
        resp = client.get(f"/roles/{opp['id']}/sourced-candidates", headers=bearer(user_id=USER_A, org_id=ORG_A))
        assert resp.status_code == 200
        rows = resp.get_json()
        assert len(rows) == 1
        sm = rows[0]["source_metadata"]
        assert sm["provider"] == "pdl" and sm["has_email"] is True
        body = resp.get_data(as_text=True)
        for secret in ("prospect@example.com", "+353860000111", "linkedin.com", "pdl-1"):
            assert secret not in body, secret

    def test_creator_without_org_claim_sees_own_role(self, client, fake_db):
        opp = seed_opportunity(fake_db, org_id=ORG_A, created_by=USER_A)
        seed_profile(fake_db, opp["id"])
        assert client.get(f"/roles/{opp['id']}/sourced-candidates",
                          headers=bearer(user_id=USER_A, org_id=None)).status_code == 200

    def test_unknown_malformed_and_unauthenticated(self, client, fake_db):
        assert client.get(f"/roles/{uuid.uuid4()}/sourced-candidates", headers=bearer()).status_code == 404
        assert client.get("/roles/not-a-uuid/sourced-candidates", headers=bearer()).status_code == 404
        assert client.get(f"/roles/{uuid.uuid4()}/sourced-candidates").status_code == 401
        assert client.get(f"/roles/{uuid.uuid4()}/sourced-candidates", headers=SERVICE_HEADERS).status_code == 404


# ── SEC-E-007: shortlist ownership and token-gated share link ───────────────

class TestSecE007Shortlists:
    def _create(self, client, opp_id, candidate_ids, headers):
        return client.post(f"/admin/roles/{opp_id}/create-shortlist",
                           json={"candidate_ids": candidate_ids}, headers=headers)

    def test_other_tenant_cannot_create_shortlist(self, client, fake_db):
        opp = seed_opportunity(fake_db, org_id=ORG_B, created_by=USER_B)
        prof = seed_profile(fake_db, opp["id"])
        resp = self._create(client, opp["id"], [prof["id"]], bearer(user_id=USER_A, org_id=ORG_A))
        assert resp.status_code == 404
        assert fake_db.store.get("shortlists", []) == []

    def test_unknown_candidate_ids_are_rejected(self, client, fake_db):
        opp = seed_opportunity(fake_db)
        resp = self._create(client, opp["id"], [str(uuid.uuid4())], bearer())
        assert resp.status_code == 400
        assert self._create(client, opp["id"], ["not-a-uuid"], bearer()).status_code == 400

    def test_owner_gets_tokenised_link_and_public_read_needs_token(self, client, fake_db):
        opp = seed_opportunity(fake_db)
        prof = seed_profile(fake_db, opp["id"], first_name="Ada", last_name="Lovelace", bio="private bio")
        seed_screening_job(fake_db, role_id=opp["id"], source_candidate_id=prof["id"])
        resp = self._create(client, opp["id"], [prof["id"]], bearer())
        assert resp.status_code == 201, resp.get_json()
        body = resp.get_json()
        sid, tok = body["shortlist_id"], body["share_token"]
        assert body["path"] == f"/shortlist/{sid}?t={tok}"
        assert len(tok) >= 32

        # The row id alone opens nothing.
        for query in ("", "?t=", "?t=" + "0" * len(tok), f"?t={tok[:-1]}x"):
            r = client.get(f"/shortlist/{sid}{query}")
            assert r.status_code == 404, query
            assert "Lovelace" not in r.get_data(as_text=True)
        assert client.post(f"/shortlist/{sid}/request-intro",
                           json={"name": "X", "email": "x@example.com"}).status_code == 404

        r = client.get(f"/shortlist/{sid}?t={tok}")
        assert r.status_code == 200
        page = r.get_json()
        assert page["candidates"][0]["name"] == "Ada Lovelace"
        assert page["candidates"][0]["recommendation"] == "proceed"
        # Transcripts are never part of the public page.
        assert "transcript" not in r.get_data(as_text=True)

        r = client.post(f"/shortlist/{sid}/request-intro?t={tok}",
                        json={"name": "X", "email": "x@example.com", "candidate_id": prof["id"]})
        assert r.status_code == 201
        assert client.post(f"/shortlist/{sid}/request-intro?t={tok}",
                           json={"name": "X", "email": "x@example.com",
                                 "candidate_id": str(uuid.uuid4())}).status_code == 400

    def test_admin_can_shortlist_any_opportunity(self, client, fake_db):
        opp = seed_opportunity(fake_db, org_id=ORG_B, created_by=USER_B)
        prof = seed_profile(fake_db, opp["id"])
        fake_db.store["role_assignments"] = [{"user_id": ADMIN, "role": "admin"}]
        resp = self._create(client, opp["id"], [prof["id"]], bearer(user_id=ADMIN, org_id=None))
        assert resp.status_code == 201

    def test_share_token_is_bound_to_shortlist_id(self):
        from routes.shortlist import make_share_token, verify_share_token
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        assert verify_share_token(a, make_share_token(a))
        assert not verify_share_token(b, make_share_token(a))
        assert not verify_share_token(a, None)

    def test_public_read_does_not_join_transcripts(self):
        with open(os.path.join(ROUTES_DIR, "shortlist.py"), encoding="utf-8") as f:
            assert "transcript_text" not in f.read()


# ── SEC-E-006: Art 86 feedback requests ─────────────────────────────────────

class TestSecE006FeedbackRequest:
    @pytest.fixture
    def sent(self, monkeypatch):
        calls = []
        import modules.email_sender as email_sender
        monkeypatch.setattr(email_sender, "send_screening_feedback_email",
                            lambda **kw: calls.append(kw) or True)
        return calls

    def test_every_outcome_is_the_same_202(self, client, fake_db, sent):
        seed_screening_job(fake_db, phone="+353861111111", candidate_email="victim@example.com")
        seed_screening_job(fake_db, phone="+353862222222")                 # no e-mail on record
        seed_screening_job(fake_db, phone="+353863333333", interaction=False)  # no scores
        bodies = []
        for phone in ("+353861111111", "+353862222222", "+353863333333", "+353860000000"):
            resp = client.post("/screening/feedback-request", json={"phone": phone, "email": "attacker@evil.test"})
            assert resp.status_code == 202, phone
            bodies.append(resp.get_json())
        assert len({json.dumps(b, sort_keys=True) for b in bodies}) == 1
        # Only the candidate with an address on record got mail, and only there.
        assert [c["candidate_email"] for c in sent] == ["victim@example.com"]
        assert "victim@example.com" not in json.dumps(bodies)

    def test_candidate_token_proves_ownership(self, client, fake_db, sent):
        job = seed_screening_job(fake_db, phone="+353869999999", candidate_email="owner@example.com")
        tok = fake_db.store["interactions"][0]["candidate_token"]
        resp = client.post("/screening/feedback-request", json={"candidate_token": tok})
        assert resp.status_code == 202
        assert [c["candidate_email"] for c in sent] == ["owner@example.com"]
        assert client.post("/screening/feedback-request",
                           json={"candidate_token": str(uuid.uuid4())}).status_code == 202
        assert len(sent) == 1

    def test_repeat_requests_are_throttled(self, client, fake_db, sent):
        seed_screening_job(fake_db, phone="+353861111111", candidate_email="victim@example.com")
        for _ in range(3):
            assert client.post("/screening/feedback-request",
                               json={"phone": "+353861111111"}).status_code == 202
        assert len(sent) == 1


# ── GDPR-E-005: callbacks are allow-listed and signed ───────────────────────

class TestGdprE005Callbacks:
    @pytest.fixture
    def posted(self, monkeypatch):
        calls = []
        import services.screening_service as ss
        import services.voice_call_service as vcs

        class R:
            status_code = 200
            text = "ok"

        monkeypatch.setattr(ss.http_requests, "post", lambda url, **kw: calls.append((url, kw)) or R())
        monkeypatch.setattr(vcs.http_requests, "post", lambda url, **kw: calls.append((url, kw)) or R())
        return calls

    def test_policy(self, monkeypatch):
        from services.callback_policy import validate_callback_url
        assert validate_callback_url("https://ainm.ai/hiring/screening-callback") is None
        assert validate_callback_url("https://api.ainm.ai/cb") is None
        assert validate_callback_url("http://ainm.ai/cb")
        assert validate_callback_url("https://evil.example/cb")
        assert validate_callback_url("https://ainm.ai.evil.example/cb")
        assert validate_callback_url("https://user:pw@ainm.ai/cb")
        assert validate_callback_url("http://169.254.169.254/latest/meta-data")
        assert validate_callback_url(None)
        monkeypatch.setenv("CALLBACK_ALLOWED_HOSTS", "hooks.example.org")
        assert validate_callback_url("https://hooks.example.org/x") is None
        assert validate_callback_url("https://ainm.ai/cb")

    def test_user_jobs_cannot_set_callback_url(self, client, fake_db, monkeypatch):
        import services.billing_service as billing_service
        monkeypatch.setattr(billing_service, "check_quota", lambda *a, **k: (True, None))
        body = {"candidate_phone": "+353861234567", "candidate_name": "S", "role_title": "R",
                "company_name": "C", "questions": [{"question": "q"}], "callback_url": "https://ainm.ai/cb"}
        resp = client.post("/screening/screen_candidate", json=body, headers=bearer())
        assert resp.status_code == 400
        assert fake_db.store.get("outbound_call_jobs", []) == []
        resp = client.post("/voice-call/exit-interview", headers=bearer(), json={
            "employee_phone": "+353861234567", "employee_name": "E", "company_name": "C",
            "role_title": "R", "tenure": "1y", "callback_url": "https://ainm.ai/cb"})
        assert resp.status_code == 400

    def test_service_jobs_need_allow_listed_https_host(self, client, fake_db):
        body = {"candidate_phone": "+353861234567", "candidate_name": "S", "role_title": "R",
                "company_name": "C", "questions": [{"question": "q"}]}
        for bad_url in ("https://evil.example/cb", "http://ainm.ai/cb"):
            resp = client.post("/screening/screen_candidate", json={**body, "callback_url": bad_url},
                               headers=SERVICE_HEADERS)
            assert resp.status_code == 400, bad_url
        resp = client.post("/screening/screen_candidate", json={**body, "callback_url": "https://ainm.ai/cb"},
                           headers=SERVICE_HEADERS)
        assert resp.status_code == 201
        ctx = fake_db.store["outbound_call_jobs"][0]["artifacts"]["screening_context"]
        assert ctx["callback_url"] == "https://ainm.ai/cb" and ctx["sign_callback"] is True

    def test_delivery_only_signed_and_allow_listed(self, env, fake_db, posted):
        from services import screening_service, voice_call_service
        screening_service._fire_callback("https://evil.example/cb", {"transcript": "secret"}, "j1", sign=True)
        screening_service._fire_callback("https://ainm.ai/cb", {"transcript": "secret"}, "j1", sign=False)
        voice_call_service._fire_callback("https://evil.example/cb", {"transcript": "secret"}, "j2", sign=True)
        assert posted == []
        screening_service._fire_callback("https://ainm.ai/cb", {"transcript": "ok"}, "j1", sign=True)
        assert len(posted) == 1 and "X-Execflex-Signature" in posted[0][1]["headers"]

    def test_blocked_delivery_is_recorded_on_the_job(self, env, fake_db, posted):
        from services import screening_service
        job = seed_screening_job(fake_db)
        screening_service._fire_callback("https://evil.example/cb", {"a": 1}, job["id"], sign=True)
        assert "allow-list" in fake_db.store["outbound_call_jobs"][0]["artifacts"]["callback_error"]["error"]
        assert posted == []


# ── GDPR-E-001: explicit consent events, nothing inferred ───────────────────

class TestGdprE001Consent:
    TRANSCRIPT = ("Assistant: I am an AI assistant. Is that all OK, and are you happy to proceed?\n"
                  "User: no, I would rather not, I said yesterday I'd look at it.\n")

    def _audit(self, fake_db, consent_event):
        from services.screening_service import _log_bias_audit
        return _log_bias_audit(
            interaction_id=str(uuid.uuid4()), job_id=str(uuid.uuid4()), role_id=None,
            role_title="R", company_name="C", questions=[{"question": "Q"}],
            scores=[{"question": "Q", "score": 3}], overall_score=3.0, recommendation="hold",
            bias_flags=[], transcript=self.TRANSCRIPT, call_duration_seconds=90,
            organization_id=ORG_A, consent_event=consent_event,
        )

    def test_scripted_lines_no_longer_count_as_consent(self, fake_db):
        assert self._audit(fake_db, None) is None
        row = fake_db.store["screening_bias_audit"][0]
        assert row["candidate_consented"] is None
        assert row["ai_disclosure_given"] is True   # Aidan did disclose

    def test_recorded_event_is_the_only_source(self, fake_db):
        assert self._audit(fake_db, {"consented": False, "at": "2026-10-16T10:00:00+00:00"}) is False
        assert self._audit(fake_db, {"consented": True, "at": "2026-10-16T10:00:00+00:00"}) is True
        rows = fake_db.store["screening_bias_audit"]
        assert [r["candidate_consented"] for r in rows] == [False, True]

    def test_no_keyword_inference_remains(self):
        with open(os.path.join(ROOT, "services", "screening_service.py"), encoding="utf-8") as f:
            source = f.read()
        for phrase in ('"sounds good"', '"go ahead"', '"yeah"'):
            assert phrase not in source

    def test_record_consent_event_is_first_answer_wins(self, fake_db):
        from services.screening_service import record_consent_event, consent_event_for_job, CONSENT_WORDING_VERSION
        job = seed_screening_job(fake_db)
        event = record_consent_event(job["id"], True, turn_sequence=3, call_sid="CA1")
        assert event["consented"] is True and event["wording_version"] == CONSENT_WORDING_VERSION
        assert event["at"]
        record_consent_event(job["id"], False)
        stored = consent_event_for_job(fake_db.store["outbound_call_jobs"][0])
        assert stored["consented"] is True

    def test_bridge_tool_handler_records_and_replies(self, fake_db):
        import threading
        from routes.voice_websocket import _handle_consent_signal, RECORD_CONSENT_TOOL
        job = seed_screening_job(fake_db)
        sent = []

        class WS:
            def send(self, data):
                sent.append(json.loads(data))

        state, lock, logs = {"next_transcript_turn_sequence": 4}, threading.Lock(), []
        item = {"type": "function_call", "name": "record_consent", "call_id": "c1",
                "arguments": json.dumps({"consented": False, "candidate_words": "no thanks"})}
        assert _handle_consent_signal(item, job["id"], "CA1", state, lock, logs.append, WS())
        event = fake_db.store["outbound_call_jobs"][0]["artifacts"]["consent_event"]
        assert event["consented"] is False and event["turn_sequence"] == 4 and event["call_sid"] == "CA1"
        assert [m["type"] for m in sent] == ["conversation.item.create", "response.create"]
        assert state["consent_given"] is False
        # Unrelated items are ignored.
        assert not _handle_consent_signal({"type": "function_call", "name": "end_call"}, job["id"], "CA1",
                                          state, lock, logs.append, WS())
        assert RECORD_CONSENT_TOOL["parameters"]["required"] == ["consented"]

    def test_screening_prompt_calls_the_tool_before_questions(self):
        from routes.voice_websocket import _get_system_prompt
        prompt = _get_system_prompt(None, call_type="screening", screening_context={
            "candidate_name": "Synthetic Candidate", "role_title": "CFO", "company_name": "Co",
            "questions": [{"question": "q1"}, {"question": "q2"}, {"question": "q3"}]})
        assert "record_consent" in prompt
        assert prompt.index("record_consent") < prompt.index("SCREENING QUESTIONS")

    def test_declined_call_is_not_scored_and_transcript_is_purged(self, env, fake_db, monkeypatch):
        import services.screening_service as ss
        monkeypatch.setattr(ss, "gpt_client", object())
        job = seed_screening_job(fake_db, callback_url="https://ainm.ai/cb", sign_callback=True)
        job["artifacts"]["consent_event"] = {"consented": False, "at": "2026-10-16T10:00:00+00:00"}
        posted = []

        class R:
            status_code = 200
            text = "ok"
        monkeypatch.setattr(ss.http_requests, "post", lambda url, **kw: posted.append((url, kw)) or R())

        assert ss.score_screening_call(job["interaction_id"], job["id"]) is None
        assert fake_db.store["interaction_turns"] == []
        ix = fake_db.store["interactions"][0]
        assert ix["transcript_text"] is None and ix["screening_scores"] is None and ix["candidate_token"] is None
        assert ix["screening_recommendation"] == "declined"
        payload = json.loads(posted[0][1]["data"])
        assert payload["call_status"] == "declined" and "transcript" not in payload

    def test_v1_phone_sync_copies_the_explicit_consent(self, fake_db):
        from routes.api_v1.screens import _apply_phone_sync
        session = {"id": "s1", "state": "in_progress", "metadata": {}}
        fake_db.store["screening_sessions"] = [{"id": "s1", "organization_id": ORG_A, "consent_given": False}]
        status = {"status": "completed", "scores": [{"score": 4, "response_summary": "x"}],
                  "recommendation": "proceed", "consent": {"consented": True, "at": "t"}}
        _apply_phone_sync(ORG_A, session, status)
        assert fake_db.store["screening_sessions"][0]["consent_given"] is True
        session2 = {"id": "s1", "state": "in_progress", "metadata": {}}
        _apply_phone_sync(ORG_A, session2, {"status": "completed", "scores": [{"score": 4}], "recommendation": "hold"})
        assert fake_db.store["screening_sessions"][0]["consent_given"] is False


# ── GDPR-E-002: erasure keyed on any identifier ─────────────────────────────

class TestGdprE002Erasure:
    def test_erasure_by_phone_reaches_sourced_candidate_and_transcripts(self, fake_db):
        from services.compliance.erasure import erase_candidate
        prof = seed_profile(fake_db, source_metadata={"enriched_phone": "+353861111111",
                                                      "enriched_email": "gone@example.com"})
        keep = seed_profile(fake_db, source_metadata={"enriched_email": "stays@example.com"})
        job = seed_screening_job(fake_db, phone="+353861111111")
        other = seed_screening_job(fake_db, phone="+353869999999")
        fake_db.store["screening_feedback_requests"] = [
            {"id": "f1", "candidate_email": "gone@example.com"}, {"id": "f2", "candidate_email": "other@example.com"}]

        report = erase_candidate(phone="+353861111111")

        assert [p["id"] for p in fake_db.store["people_profiles"]] == [keep["id"]]
        assert [j["id"] for j in fake_db.store["outbound_call_jobs"]] == [other["id"]]
        assert all(t["interaction_id"] != job["interaction_id"] for t in fake_db.store["interaction_turns"])
        scrubbed = next(i for i in fake_db.store["interactions"] if i["id"] == job["interaction_id"])
        assert scrubbed["transcript_text"] is None and scrubbed["candidate_token"] is None
        untouched = next(i for i in fake_db.store["interactions"] if i["id"] == other["interaction_id"])
        assert untouched["transcript_text"]
        assert [f["id"] for f in fake_db.store["screening_feedback_requests"]] == ["f2"]
        sup = fake_db.store["contact_suppressions"]
        assert {s["email"] for s in sup if s["email"]} == {"gone@example.com"}
        assert {s["phone"] for s in sup if s["phone"]} == {"+353861111111"}
        assert report["deleted"]["people_profiles"] == 1 and report["identifiers"]["profile_ids"] == [prof["id"]]
        assert report["warnings"] == []

    def test_erasure_by_user_id_expands_through_channel_identities(self, fake_db):
        from services.compliance.erasure import erase_candidate
        fake_db.store["channel_identities"] = [
            {"id": "c1", "user_id": USER_A, "channel": "email", "value": "me@example.com"},
            {"id": "c2", "user_id": USER_A, "channel": "phone", "value": "+353867777777"},
            {"id": "c3", "user_id": USER_B, "channel": "email", "value": "other@example.com"},
        ]
        seed_screening_job(fake_db, phone="+353867777777")
        erase_candidate(user_id=USER_A)
        assert [c["id"] for c in fake_db.store["channel_identities"]] == ["c3"]
        assert fake_db.store["outbound_call_jobs"] == []

    def test_completing_an_erasure_request_erases(self, fake_db, monkeypatch):
        from services.compliance.data_rights import process_data_rights_request
        seed_profile(fake_db, source_metadata={"upload_email": "req@example.com"})
        fake_db.store["data_rights_requests"] = [{
            "id": "r1", "organization_id": ORG_A, "request_type": "erasure",
            "requester_email": "req@example.com", "status": "pending"}]
        row = process_data_rights_request(ORG_A, "r1", "completed", completed_by=USER_A, notes="done")
        assert fake_db.store["people_profiles"] == []
        assert row["fulfilment"]["deleted"]["people_profiles"] == 1
        assert "fulfilment:" in fake_db.store["data_rights_requests"][0]["notes"]
        assert fake_db.store["data_rights_requests"][0]["status"] == "completed"

    def test_status_change_alone_does_not_erase(self, fake_db):
        from services.compliance.data_rights import process_data_rights_request
        seed_profile(fake_db, source_metadata={"upload_email": "req@example.com"})
        fake_db.store["data_rights_requests"] = [{
            "id": "r1", "organization_id": ORG_A, "request_type": "erasure",
            "requester_email": "req@example.com", "status": "pending"}]
        process_data_rights_request(ORG_A, "r1", "in_progress")
        assert len(fake_db.store["people_profiles"]) == 1
        # Another organisation's request id never matches.
        assert process_data_rights_request(ORG_B, "r1", "completed") == {}
        assert len(fake_db.store["people_profiles"]) == 1

    def test_admin_delete_user_by_phone_without_account_erases(self, client, fake_db, monkeypatch):
        import routes.onboarding as onboarding
        fake_db.store["role_assignments"] = [{"user_id": ADMIN, "role": "admin"}]

        class R:
            status_code = 200
            text = "{}"

            def json(self):
                return {"users": []}
        monkeypatch.setattr(onboarding.requests, "get", lambda *a, **k: R())
        seed_profile(fake_db, source_metadata={"upload_phone": "+353865555555"})
        resp = client.post("/onboarding/delete-user", json={"phone": "+353865555555"},
                           headers=bearer(user_id=ADMIN, org_id=None))
        assert resp.status_code == 200, resp.get_json()
        assert fake_db.store["people_profiles"] == []
        assert resp.get_json()["erasure"]["deleted"]["people_profiles"] == 1


# ── GDPR-E-003: no PII in logs ──────────────────────────────────────────────

class TestGdprE003Logs:
    def test_masks(self):
        from utils.log_redaction import mask_email, mask_phone
        assert mask_email("jane.doe@example.com") == "j***@example.com"
        assert mask_phone("+353861234567") == "+353***4567"
        assert "861234" not in mask_phone("+353861234567")
        assert mask_email(None) == "<none>" and mask_phone("") == "<none>"

    def test_pii_log_lines_are_gone(self):
        checks = {
            "routes/talent_network.py": ["email={email}", "phone={phone}"],
            "routes/roles.py": ['print("🚀 /post-role payload:", data)'],
            "routes/voice.py": ["transcript_text[:300]"],
            "services/call_extraction_service.py": ["transcript[:500]"],
            "routes/voice_websocket.py": ["system_prompt[:500]", "text[:80]"],
            "routes/cara_websocket.py": ["system_prompt[:500]", "system_prompt[-500:]"],
            "routes/introductions.py": ["phone={phone} role"],
            "services/onboarding_service.py": ["phone={phone}"],
            "routes/shortlist.py": ["email={email}"],
            "routes/onboarding.py": ["by phone: {phone}"],
            "services/screening_service.py": ["candidate_token={candidate_token}"],
            "routes/health.py": ["email={email!r}"],
        }
        for path, needles in checks.items():
            with open(os.path.join(ROOT, path), encoding="utf-8") as f:
                source = f.read()
            for needle in needles:
                assert needle not in source, f"{path} still logs {needle!r}"

    def test_shortlist_intro_log_is_masked(self, client, fake_db, capsys):
        from routes.shortlist import make_share_token
        opp = seed_opportunity(fake_db)
        fake_db.store["shortlists"] = [{"id": str(uuid.uuid4()), "opportunity_id": opp["id"],
                                       "candidate_ids": [], "expires_at": None}]
        sid = fake_db.store["shortlists"][0]["id"]
        client.post(f"/shortlist/{sid}/request-intro?t={make_share_token(sid)}",
                    json={"name": "Req", "email": "requester@example.com"})
        out = capsys.readouterr().out
        assert "requester@example.com" not in out
        assert "r***@example.com" in out


# ── GDPR-E-004: legal-basis gate and suppression list ───────────────────────

class TestGdprE004SourcingGate:
    def test_gate_reads_env(self, monkeypatch):
        from services.compliance.legal_basis import sourcing_allowed, stamp_legal_basis
        monkeypatch.delenv("SOURCING_LEGAL_BASIS", raising=False)
        monkeypatch.delenv("SOURCING_LIA_REF", raising=False)
        assert not sourcing_allowed()
        monkeypatch.setenv("SOURCING_LEGAL_BASIS", "legitimate_interest")
        assert not sourcing_allowed()
        monkeypatch.setenv("SOURCING_LIA_REF", "iso/LIA-2026-01")
        assert sourcing_allowed()
        sm = stamp_legal_basis({"apollo_id": "x"}, "2026-10-16T00:00:00+00:00")
        assert sm["legal_basis"] == "legitimate_interest" and sm["lia_ref"] == "iso/LIA-2026-01"
        assert sm["sourced_at"] and sm["apollo_id"] == "x"

    def test_sourcing_does_nothing_without_a_basis(self, env, fake_db, monkeypatch):
        import services.sourcing_service as sourcing
        monkeypatch.setattr(sourcing, "search_candidates",
                            lambda **k: (_ for _ in ()).throw(AssertionError("PDL must not be called")))
        assert sourcing.source_and_upsert(str(uuid.uuid4()), "CFO") == 0

    def test_sourced_rows_are_stamped_with_the_basis(self, env, fake_db, monkeypatch):
        import services.sourcing_service as sourcing
        monkeypatch.setenv("SOURCING_LEGAL_BASIS", "legitimate_interest")
        monkeypatch.setenv("SOURCING_LIA_REF", "iso/LIA-2026-01")
        monkeypatch.setattr(sourcing, "search_candidates", lambda **k: [{
            "name": "New Person", "headline": "CFO", "location": None, "years_experience": 10,
            "linkedin_url": None, "approved": False, "source": "apollo",
            "source_metadata": {"apollo_id": "p1", "opportunity_id": "o1"}}])
        assert sourcing.source_and_upsert("o1", "CFO") == 1
        sm = fake_db.store["people_profiles"][0]["source_metadata"]
        assert sm["lia_ref"] == "iso/LIA-2026-01" and sm["sourced_at"]

    def test_enrich_and_outreach_blocked_without_basis(self, client, fake_db):
        fake_db.store["role_assignments"] = [{"user_id": ADMIN, "role": "admin"}]
        opp = seed_opportunity(fake_db)
        prof = seed_profile(fake_db, opp["id"])
        for path in ("enrich-candidates", "send-outreach"):
            resp = client.post(f"/admin/roles/{opp['id']}/{path}", json={"candidate_ids": [prof["id"]]},
                               headers=bearer(user_id=ADMIN, org_id=None))
            assert resp.status_code == 403, path
            assert resp.get_json()["error_code"] == "legal_basis_required"

    def test_outreach_skips_suppressed_and_carries_art14_notice(self, client, fake_db, monkeypatch):
        import routes.billing as billing
        import modules.email_sender as email_sender
        import services.outreach_service as outreach
        monkeypatch.setenv("SOURCING_LEGAL_BASIS", "legitimate_interest")
        monkeypatch.setenv("SOURCING_LIA_REF", "iso/LIA-2026-01")
        sent = []
        monkeypatch.setattr(email_sender, "send_intro_email", lambda **kw: sent.append(kw) or True)
        monkeypatch.setattr(outreach, "generate_outreach_email", lambda *a, **k: {"subject": "s", "body": "b"})
        fake_db.store["role_assignments"] = [{"user_id": ADMIN, "role": "admin"}]
        opp = seed_opportunity(fake_db)
        ok_prof = seed_profile(fake_db, opp["id"], source_metadata={"enriched_email": "ok@example.com"})
        sup_prof = seed_profile(fake_db, opp["id"], source_metadata={"enriched_email": "no@example.com"})
        fake_db.store["contact_suppressions"] = [{"id": "s1", "email": "no@example.com", "phone": None}]
        resp = client.post(f"/admin/roles/{opp['id']}/send-outreach",
                           json={"candidate_ids": [ok_prof["id"], sup_prof["id"]]},
                           headers=bearer(user_id=ADMIN, org_id=None))
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["sent"] == [ok_prof["id"]]
        assert {"id": sup_prof["id"], "reason": "suppressed"} in body["skipped"]
        assert len(sent) == 1 and sent[0]["candidate_email"] == "ok@example.com"
        assert "People Data Labs" in sent[0]["plain_body_override"]
        assert "iso/LIA-2026-01" in sent[0]["plain_body_override"]

    def test_suppressed_phone_cannot_be_dialled(self, client, fake_db):
        fake_db.store["contact_suppressions"] = [{"id": "s1", "email": None, "phone": "+353861234567"}]
        body = {"candidate_phone": "+353 86 123 4567", "candidate_name": "S", "role_title": "R",
                "company_name": "C", "questions": [{"question": "q"}]}
        resp = client.post("/screening/screen_candidate", json=body, headers=SERVICE_HEADERS)
        assert resp.status_code == 409
        assert fake_db.store.get("outbound_call_jobs", []) == []
        from services.screening_service import create_screening_job
        with pytest.raises(ValueError):
            create_screening_job("+353861234567", "S", "R", "C", [{"question": "q"}], None, None)

    def test_not_interested_reply_suppresses_globally(self, client, fake_db):
        thread_id = str(uuid.uuid4())
        fake_db.store["threads"] = [{"id": thread_id, "primary_user_id": None, "opportunity_id": "o1", "subject": "s"}]
        fake_db.store["interactions"] = [{"id": "ix", "thread_id": thread_id,
                                         "artifacts": {"candidate_email": "Bye@Example.com", "candidate_profile_id": "p1"}}]
        resp = client.get(f"/intro/respond?thread_id={thread_id}&action=notinterested")
        assert resp.status_code == 200
        sup = fake_db.store["contact_suppressions"]
        assert len(sup) == 1 and sup[0]["email"] == "bye@example.com" and sup[0]["reason"] == "not_interested"
        from services.compliance.suppression import is_suppressed
        assert is_suppressed(email="BYE@example.com")
        assert not is_suppressed(email="someone-else@example.com")

    def test_suppression_survives_missing_table(self, fake_db, monkeypatch):
        from services.compliance import suppression

        class Boom(FakeSupabase):
            def table(self, name):
                if name == suppression.TABLE:
                    raise RuntimeError("relation does not exist")
                return super().table(name)
        import config.clients as clients
        monkeypatch.setattr(clients, "supabase_client", Boom())
        assert suppression.is_suppressed(email="x@example.com") is False
        assert suppression.suppress(email="x@example.com")["added"] is False
