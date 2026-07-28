"""ainm Marketplace API — a REAL two-sided marketplace for pre-vetted AI/data leaders.

A NEW product surface (see DECISIONS.md D-14/D-15/D-17). Reuses the console's
org-scoped JWT auth but is otherwise separate from the recruiter product.

Two real sides, both on real auth accounts:
  SUPPLY (leaders)  — sign up, build a profile linked to their account, take the
                      vetting assessment, appear in search when verified, edit
                      their profile, see introduction requests, accept/decline,
                      and export/delete their data (GDPR).
  DEMAND (companies) — sign up, search the pool, view verified profiles + vetting
                      rationale, request an introduction (real row, fee terms),
                      track their introductions, and record an outcome.

Privacy: a leader's contact details are revealed to a company only after the
leader accepts that company's introduction. Tenant isolation: a company sees
only its own introductions; a leader sees only requests addressed to them.

Routes (all under /api/v1/marketplace):
  GET   /search                         ranked, explainable search (facets + free-text)
  GET   /facets                         available search facets (skills/sectors/…)
  GET   /leaders                        browse the vetted pool (filters)
  GET   /leaders/<id>                   leader profile (+ contact if you own it)
  POST  /leaders                        supply side: create/claim your profile
  PATCH /leaders/<id>                   edit your own profile
  GET   /me                             your marketplace context (leader/company)
  GET   /me/export                      GDPR: export everything held about you
  DELETE /me                            GDPR: erase your leader profile
  GET   /vetting/questions              the fixed question set for a track
  POST  /leaders/<id>/vetting           submit responses → score → verify/reject
  GET   /opportunities                  marketplace roles (companies + roles)
  GET   /companies                      distinct demand-side companies
  GET   /company                        your company profile
  PUT   /company                        create/update your company profile
  POST  /leaders/<id>/introductions     request an introduction (billable)
  GET   /introductions                  YOUR introductions (company, tenant-scoped)
  GET   /admin/introductions            operator pipeline (all) — admin only
  GET   /inbox                          introduction requests addressed to you (leader)
  POST  /introductions/<id>/respond     leader accepts/declines a request
  PATCH /introductions/<id>             company records outcome (hired / not proceeding)
  POST  /seed                           (owner) load the synthetic demo pool
  DELETE /seed                          (owner) purge the marketplace namespace
"""
import threading
import time

from flask import request

from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error
from services.marketplace import store
from services.marketplace import validation as V
from services.marketplace.constants import (
    DEFAULT_PLACEMENT_FEE_PCT, VETTING_TRACKS, INTRO_STATES, TRACK_LABELS,
    MARKETPLACE_ORG_ID, COMPANY_OUTCOME_STATES,
    VETTING_IP_LIMIT, VETTING_IP_WINDOW_S, VETTING_LEADER_LIMIT, VETTING_LEADER_WINDOW_S,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _is_marketplace_admin(ctx) -> bool:
    """Platform operator check for the all-tenants pipeline view.

    True when operating AS the marketplace org, or when the user id is in the
    MARKETPLACE_ADMIN_USER_IDS env allowlist. Regular buyer companies (owners of
    their own org) are NOT admins and only ever see their own introductions.
    """
    import os
    if ctx.org_id == MARKETPLACE_ORG_ID:
        return True
    allow = {u.strip() for u in os.environ.get("MARKETPLACE_ADMIN_USER_IDS", "").split(",") if u.strip()}
    return ctx.user_id in allow


# In-memory sliding-window rate limiters (process-local; same pattern as
# routes/talent_network.py). Guards the vetting endpoint against assessment farming.
_ip_buckets: dict = {}
_leader_buckets: dict = {}
_rl_lock = threading.Lock()


def _rate_ok(bucket: dict, key: str, limit: int, window_s: int) -> bool:
    now = time.time()
    cutoff = now - window_s
    with _rl_lock:
        ts = [t for t in bucket.get(key, []) if t > cutoff]
        if len(ts) >= limit:
            bucket[key] = ts
            return False
        ts.append(now)
        bucket[key] = ts
        return True


# ── Search (the "search marketplace") ────────────────────────────────────────

@api_v1_bp.route('/marketplace/search', methods=['GET'])
@require_org()
def marketplace_search():
    from services.marketplace.search import search_leaders
    args = request.args

    def _num(name):
        v = args.get(name)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    use_ai = None
    if args.get("ai") in ("1", "true", "on"):
        use_ai = True
    elif args.get("ai") in ("0", "false", "off"):
        use_ai = False

    try:
        limit = max(1, min(100, int(args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50

    result = search_leaders(
        query=args.get("q", ""),
        skill=args.get("skill"),
        track=args.get("track") if args.get("track") in VETTING_TRACKS else None,
        seniority=args.get("seniority"),
        engagement=args.get("engagement"),
        sector=args.get("sector"),
        comp_min=_num("comp_min"),
        comp_max=_num("comp_max"),
        limit=limit,
        use_ai=use_ai,
    )
    return api_ok(result)


@api_v1_bp.route('/marketplace/facets', methods=['GET'])
@require_org()
def marketplace_facets():
    """Distinct facet values across the verified pool, for search UI chips."""
    leaders = store.list_leaders(status="verified", limit=1000)
    skills, sectors, seniorities = {}, {}, set()
    for ld in leaders:
        for s in ld.get("skills") or []:
            skills[s] = skills.get(s, 0) + 1
        for s in ld.get("sectors") or []:
            sectors[s] = sectors.get(s, 0) + 1
        if ld.get("seniority"):
            seniorities.add(ld["seniority"])
    top = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: kv[1], reverse=True)]
    return api_ok({
        "skills": top(skills)[:40],
        "sectors": top(sectors),
        "seniorities": sorted(seniorities),
        "tracks": [{"value": t, "label": TRACK_LABELS[t]} for t in VETTING_TRACKS],
        "engagement": ["fractional", "permanent", "both"],
    })


# ── Demand side: browse the vetted pool ──────────────────────────────────────

@api_v1_bp.route('/marketplace/leaders', methods=['GET'])
@require_org()
def marketplace_list_leaders():
    args = request.args
    status = args.get("status", "verified")
    if status == "all":
        status = None
    leaders = store.list_leaders(
        status=status,
        skill=args.get("skill"),
        seniority=args.get("seniority"),
        engagement=args.get("engagement"),
        sector=args.get("sector"),
        track=args.get("track"),
    )
    return api_ok({"leaders": leaders, "total": len(leaders)})


@api_v1_bp.route('/marketplace/leaders/<leader_id>', methods=['GET'])
@require_org()
def marketplace_get_leader(leader_id):
    ctx = get_org_context()
    leader = store.get_leader(leader_id)
    if not leader:
        return api_error("Leader not found", 404)
    # Reveal private contact only to the profile owner. Companies get a leader's
    # contact via an accepted introduction, never by browsing.
    if leader.get("user_id") and leader["user_id"] == ctx.user_id:
        leader = store.get_leader(leader_id, include_contact=True)
    return api_ok(leader)


# ── Supply side: create / claim / edit your profile ──────────────────────────

@api_v1_bp.route('/marketplace/leaders', methods=['POST'])
@require_org()
def marketplace_create_leader():
    ctx = get_org_context()
    data = request.get_json() or {}
    try:
        clean = V.validate_leader_payload(data, require_name=True)
    except V.ValidationError as e:
        return api_error(str(e), 400)

    # One leader profile per account: if the caller already has one, update it
    # instead of creating a duplicate (idempotent "claim").
    existing = store.get_leader_by_user(ctx.user_id)
    if existing:
        updated = store.update_leader(
            existing["id"],
            name=clean.get("name"), headline=clean.get("headline"),
            bio=clean.get("bio"), location=clean.get("location"),
            skills=clean.get("skills"), sectors=clean.get("sectors"),
            seniority=clean.get("seniority"), track=clean.get("track"),
            engagement=clean.get("engagement"),
            comp_expectation=clean.get("comp_expectation"),
            years_experience=clean.get("years_experience"),
            contact=clean.get("contact"),
        )
        return api_ok(updated, 200)

    leader = store.create_leader(
        name=clean["name"], headline=clean.get("headline", ""),
        bio=clean.get("bio", ""), location=clean.get("location", ""),
        skills=clean.get("skills") or [], sectors=clean.get("sectors") or [],
        seniority=clean.get("seniority", ""), track=clean["track"],
        engagement=clean.get("engagement", "both"),
        comp_expectation=clean.get("comp_expectation", ""),
        years_experience=clean.get("years_experience", 0),
        user_id=ctx.user_id, contact=clean.get("contact") or {},
        vetting_status="pending",
    )
    return api_ok(leader, 201)


@api_v1_bp.route('/marketplace/leaders/<leader_id>', methods=['PATCH'])
@require_org()
def marketplace_update_leader(leader_id):
    ctx = get_org_context()
    leader = store.get_leader(leader_id)
    if not leader:
        return api_error("Leader not found", 404)
    if leader.get("user_id") != ctx.user_id and not _is_marketplace_admin(ctx):
        return api_error("You can only edit your own profile", 403)
    data = request.get_json() or {}
    try:
        clean = V.validate_leader_payload(data, require_name=False)
    except V.ValidationError as e:
        return api_error(str(e), 400)
    updated = store.update_leader(leader_id, **clean)
    return api_ok(updated)


@api_v1_bp.route('/marketplace/me', methods=['GET'])
@require_org()
def marketplace_me():
    """The caller's marketplace context: their leader profile and/or company profile."""
    ctx = get_org_context()
    leader = store.get_leader_by_user(ctx.user_id)
    company = store.get_company_profile(ctx.org_id)
    return api_ok({
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "is_leader": bool(leader),
        "leader": leader,
        "company": company,
        "is_admin": _is_marketplace_admin(ctx),
    })


# ── GDPR: export / erase ─────────────────────────────────────────────────────

@api_v1_bp.route('/marketplace/me/export', methods=['GET'])
@require_org()
def marketplace_export_me():
    ctx = get_org_context()
    leader = store.get_leader_by_user(ctx.user_id)
    if not leader:
        return api_error("No marketplace leader profile found for your account", 404)
    return api_ok(store.export_leader(leader["id"]))


@api_v1_bp.route('/marketplace/me', methods=['DELETE'])
@require_org()
def marketplace_delete_me():
    ctx = get_org_context()
    leader = store.get_leader_by_user(ctx.user_id)
    if not leader:
        return api_error("No marketplace leader profile found for your account", 404)
    ok = store.delete_leader(leader["id"])
    return api_ok({"deleted": ok, "leader_id": leader["id"]})


# ── Vetting (the moat) ───────────────────────────────────────────────────────

@api_v1_bp.route('/marketplace/vetting/questions', methods=['GET'])
@require_org()
def marketplace_vetting_questions():
    track = request.args.get("track", "ml_platform")
    if track not in VETTING_TRACKS:
        return api_error(f"track must be one of {', '.join(VETTING_TRACKS)}", 400)
    from services.marketplace.vetting import question_set
    return api_ok({"track": track, "questions": question_set(track)})


@api_v1_bp.route('/marketplace/leaders/<leader_id>/vetting', methods=['POST'])
@require_org()
def marketplace_submit_vetting(leader_id):
    ctx = get_org_context()
    leader = store.get_leader(leader_id, include_contact=True)
    if not leader:
        return api_error("Leader not found", 404)
    # Only the profile owner (or an admin) may submit its assessment.
    if leader.get("user_id") and leader["user_id"] != ctx.user_id and not _is_marketplace_admin(ctx):
        return api_error("You can only take the assessment for your own profile", 403)

    data = request.get_json() or {}
    try:
        responses = V.validate_vetting_responses(data.get("responses"))
    except V.ValidationError as e:
        return api_error(str(e), 400)

    track = data.get("track") or leader.get("track") or "ml_platform"
    if track not in VETTING_TRACKS:
        return api_error(f"track must be one of {', '.join(VETTING_TRACKS)}", 400)

    # Rate-limit the costly scoring path: assessment-farming guard (per IP and
    # per leader profile). Applied only to well-formed attempts that would score.
    if not _rate_ok(_ip_buckets, _client_ip(), VETTING_IP_LIMIT, VETTING_IP_WINDOW_S):
        return api_error("Too many assessment submissions from this network. Please try again later.", 429)
    if not _rate_ok(_leader_buckets, leader_id, VETTING_LEADER_LIMIT, VETTING_LEADER_WINDOW_S):
        return api_error("You've reached the assessment attempt limit for today. Please try again tomorrow.", 429)

    from services.marketplace.vetting import score_vetting
    result = score_vetting(leader_name=leader["name"], track=track, responses=responses)
    vetting = result.to_dict()
    vetting["track"] = track

    updated = store.set_leader_vetting(leader_id, vetting, result.status)
    store.record_vetting_attempt(leader_id)

    # Audit the AI decision (EU AI Act Art. 13 transparency).
    try:
        from services.compliance.decision_logger import log_decision
        log_decision(
            org_id=ctx.org_id, decision_type="screening_score",
            candidate_id=leader_id, opportunity_id=None,
            inputs={"channel": "marketplace_vetting", "track": track,
                    "n_responses": len(responses)},
            model_used=result.model_used, score=round(result.score / 100.0, 2),
            explanation=result.rationale,
        )
    except Exception:
        pass

    # Notify the leader of the outcome (best-effort; only if they gave an email).
    contact = (leader.get("contact") or {})
    if contact.get("email"):
        try:
            from modules.email_sender import send_marketplace_vetting_result
            send_marketplace_vetting_result(
                leader_email=contact["email"], leader_name=leader["name"],
                passed=result.passed, score=result.score,
                threshold=vetting.get("threshold", 70), rationale=result.rationale,
                track=track,
            )
        except Exception:
            pass

    return api_ok({"leader": updated, "vetting": vetting})


# ── Opportunities & companies (demand catalog) ───────────────────────────────

@api_v1_bp.route('/marketplace/opportunities', methods=['GET'])
@require_org()
def marketplace_list_opportunities():
    opps = store.list_opportunities()
    return api_ok({"opportunities": opps, "total": len(opps)})


@api_v1_bp.route('/marketplace/companies', methods=['GET'])
@require_org()
def marketplace_list_companies():
    companies = store.list_companies()
    return api_ok({"companies": companies, "total": len(companies)})


# ── Company profile (so leaders see who's asking) ────────────────────────────

@api_v1_bp.route('/marketplace/company', methods=['GET'])
@require_org()
def marketplace_get_company():
    ctx = get_org_context()
    company = store.get_company_profile(ctx.org_id)
    if not company:
        return api_ok(None)
    return api_ok(company)


@api_v1_bp.route('/marketplace/company', methods=['PUT'])
@require_org()
def marketplace_put_company():
    ctx = get_org_context()
    data = request.get_json() or {}
    try:
        clean = V.validate_company_payload(data)
    except V.ValidationError as e:
        return api_error(str(e), 400)
    company = store.upsert_company_profile(org_id=ctx.org_id, actor_id=ctx.user_id, **clean)
    return api_ok(company)


# ── Introductions (the billable event) ───────────────────────────────────────

@api_v1_bp.route('/marketplace/leaders/<leader_id>/introductions', methods=['POST'])
@require_org()
def marketplace_request_introduction(leader_id):
    ctx = get_org_context()
    leader = store.get_leader(leader_id, include_contact=True)
    if not leader:
        return api_error("Leader not found", 404)
    if leader.get("vetting_status") != "verified":
        return api_error("Introductions can only be requested for vetted leaders", 400)

    data = request.get_json() or {}
    try:
        clean = V.validate_intro_payload(data)
    except V.ValidationError as e:
        return api_error(str(e), 400)

    # Resolve the buyer's company: prefer their saved company profile, else the
    # posted name, else their org name.
    saved_company = store.get_company_profile(ctx.org_id)
    company = {}
    if saved_company:
        company = {k: saved_company.get(k) for k in
                   ("id", "name", "sector", "size", "location", "website")}
    if clean.get("company_name"):
        company["name"] = clean["company_name"]
    if not company.get("name"):
        company = {"name": "A hiring company"}
    company["org_id"] = ctx.org_id

    fee_pct = data.get("placement_fee_pct")
    try:
        fee_pct = float(fee_pct) if fee_pct is not None else DEFAULT_PLACEMENT_FEE_PCT
    except (TypeError, ValueError):
        fee_pct = DEFAULT_PLACEMENT_FEE_PCT

    opp = store.get_opportunity(clean["opportunity_id"]) if clean.get("opportunity_id") else None

    # Requester contact so the leader can see who's asking (from company profile).
    requester_contact = {}
    if saved_company:
        requester_contact = {"name": saved_company.get("contact_name"),
                             "email": saved_company.get("contact_email"),
                             "company": saved_company.get("name")}

    intro = store.create_introduction(
        org_id=ctx.org_id, actor_id=ctx.user_id,
        leader_id=leader_id, leader_name=leader["name"], company=company,
        opportunity_id=clean.get("opportunity_id") or None,
        opportunity_title=(opp or {}).get("title") if opp else clean.get("opportunity_title"),
        message=clean.get("message", ""), first_year_comp=clean.get("first_year_comp"),
        fee_pct=fee_pct, status="requested",
        requester_contact=requester_contact,
    )

    # Notify the leader they've been requested (best-effort).
    lcontact = leader.get("contact") or {}
    if lcontact.get("email"):
        try:
            from modules.email_sender import send_marketplace_intro_request_to_leader
            send_marketplace_intro_request_to_leader(
                leader_email=lcontact["email"], leader_name=leader["name"],
                company_name=company.get("name"), role_title=intro.get("opportunity_title"),
                message=clean.get("message"),
            )
        except Exception:
            pass

    return api_ok(intro, 201)


@api_v1_bp.route('/marketplace/introductions', methods=['GET'])
@require_org()
def marketplace_list_introductions():
    """The caller's OWN introductions (tenant-scoped). Companies track requests here."""
    ctx = get_org_context()
    intros = store.list_introductions(org_id=ctx.org_id)
    return api_ok(_intro_list_payload(intros))


@api_v1_bp.route('/marketplace/admin/introductions', methods=['GET'])
@require_org()
def marketplace_admin_introductions():
    """Operator pipeline across all tenants — platform admins only."""
    ctx = get_org_context()
    if not _is_marketplace_admin(ctx):
        return api_error("Admin access required", 403)
    intros = store.list_introductions(org_id=None)
    return api_ok(_intro_list_payload(intros))


def _intro_list_payload(intros: list) -> dict:
    total_fees = sum(i["placement_fee_amount"] or 0 for i in intros if i["status"] == "hired")
    pipeline_fees = sum(i["placement_fee_amount"] or 0 for i in intros
                        if i["status"] in ("accepted", "interviewing") and i["placement_fee_amount"])
    return {
        "introductions": intros,
        "total": len(intros),
        "summary": {
            "hired": sum(1 for i in intros if i["status"] == "hired"),
            "open": sum(1 for i in intros if i["status"] in ("requested", "accepted", "interviewing")),
            "declined": sum(1 for i in intros if i["status"] == "declined"),
            "realised_fees": round(total_fees, 2),
            "pipeline_fees": round(pipeline_fees, 2),
        },
    }


# ── Leader inbox: requests addressed to me + accept/decline ──────────────────

@api_v1_bp.route('/marketplace/inbox', methods=['GET'])
@require_org()
def marketplace_inbox():
    ctx = get_org_context()
    leader = store.get_leader_by_user(ctx.user_id)
    if not leader:
        return api_error("No marketplace leader profile found for your account", 404)
    intros = store.list_introductions_for_leader(leader["id"])
    return api_ok({"introductions": intros, "total": len(intros),
                   "leader_id": leader["id"]})


@api_v1_bp.route('/marketplace/introductions/<intro_id>/respond', methods=['POST'])
@require_org()
def marketplace_respond_introduction(intro_id):
    ctx = get_org_context()
    leader = store.get_leader_by_user(ctx.user_id)
    if not leader:
        return api_error("Only a leader can respond to an introduction request", 403)
    data = request.get_json() or {}
    decision = (data.get("decision") or "").strip().lower()
    if decision not in ("accepted", "declined", "accept", "decline"):
        return api_error("decision must be 'accepted' or 'declined'", 400)
    decision = "accepted" if decision.startswith("accept") else "declined"

    leader_contact = leader.get("contact") or {}
    updated = store.respond_to_introduction(
        intro_id, leader_id=leader["id"], decision=decision,
        leader_contact=leader_contact if decision == "accepted" else None,
    )
    if not updated:
        return api_error("Introduction not found or not addressed to you", 404)

    # Notify the requesting company of the leader's decision (best-effort).
    requester = updated.get("requester_contact") or {}
    if requester.get("email"):
        try:
            from modules.email_sender import send_marketplace_intro_response_to_company
            send_marketplace_intro_response_to_company(
                company_email=requester["email"],
                company_contact_name=requester.get("name"),
                leader_name=leader["name"], accepted=(decision == "accepted"),
                leader_contact=leader_contact if decision == "accepted" else None,
            )
        except Exception:
            pass

    return api_ok(updated)


@api_v1_bp.route('/marketplace/introductions/<intro_id>', methods=['PATCH'])
@require_org()
def marketplace_update_introduction(intro_id):
    """Company records an outcome on its own introduction (interviewing/hired/closed)."""
    ctx = get_org_context()
    row = store.get_introduction(intro_id, viewer_org_id=ctx.org_id)
    if not row:
        return api_error("Introduction not found", 404)
    # Tenant isolation: only the owning company (or an admin) may update it.
    if row.get("org_id") != ctx.org_id and not _is_marketplace_admin(ctx):
        return api_error("You can only update your own introductions", 403)

    data = request.get_json() or {}
    status = data.get("status")
    if status is not None and status not in INTRO_STATES:
        return api_error(f"status must be one of {', '.join(INTRO_STATES)}", 400)
    # A company controls outcome states only (accept/decline is the leader's).
    if status in ("accepted", "declined") and not _is_marketplace_admin(ctx):
        return api_error("Only the leader can accept or decline; you can record an outcome "
                         f"({', '.join(COMPANY_OUTCOME_STATES)})", 400)

    hired = data.get("hired")
    first_year_comp = data.get("first_year_comp")
    if first_year_comp not in (None, ""):
        try:
            first_year_comp = float(first_year_comp)
        except (TypeError, ValueError):
            return api_error("first_year_comp must be a number", 400)
    else:
        first_year_comp = None
    fee_pct = data.get("placement_fee_pct")
    if fee_pct is not None:
        try:
            fee_pct = float(fee_pct)
        except (TypeError, ValueError):
            return api_error("placement_fee_pct must be a number", 400)

    outcome = data.get("outcome")
    if outcome is not None:
        outcome = str(outcome)[:200]

    intro = store.update_introduction(
        intro_id, status=status, hired=hired,
        first_year_comp=first_year_comp, fee_pct=fee_pct,
        outcome=outcome, viewer_org_id=ctx.org_id,
    )
    if not intro:
        return api_error("Introduction not found", 404)
    return api_ok(intro)


# ── Seed (demo data, one command) ────────────────────────────────────────────

@api_v1_bp.route('/marketplace/seed', methods=['POST'])
@require_org(allowed_roles=["owner"])
def marketplace_seed():
    from services.marketplace.seeder import seed
    result = seed(purge_first=True)
    return api_ok(result, 201)


@api_v1_bp.route('/marketplace/seed', methods=['DELETE'])
@require_org(allowed_roles=["owner"])
def marketplace_unseed():
    counts = store.purge_marketplace()
    return api_ok({"purged": counts})
