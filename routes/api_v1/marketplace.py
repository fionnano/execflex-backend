"""ainm Marketplace API — curated two-sided marketplace for pre-vetted AI/data leaders.

A NEW product surface (see DECISIONS.md D-14/D-15). Reuses the console's
org-scoped JWT auth but is otherwise separate from the recruiter product. The
leader pool is a shared curated catalog (global read); introductions are the
billable event, owned by the requesting company's org.

Routes (all under /api/v1/marketplace):
  GET   /leaders                     browse the vetted pool (filters)
  GET   /leaders/<id>                leader profile + vetting rationale
  POST  /leaders                     supply side: apply / join (unverified)
  GET   /vetting/questions           the fixed question set for a track
  POST  /leaders/<id>/vetting        submit responses → score → verify/reject
  GET   /opportunities               marketplace roles (companies + roles)
  GET   /companies                   distinct demand-side companies
  POST  /leaders/<id>/introductions  request an introduction (billable)
  GET   /introductions               operator pipeline (fee status)
  PATCH /introductions/<id>          update status / mark hired → fee
  POST  /seed                        (owner) load the synthetic demo pool
  DELETE /seed                       (owner) purge the marketplace namespace
"""
from flask import request

from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error
from services.marketplace import store
from services.marketplace.constants import (
    DEFAULT_PLACEMENT_FEE_PCT, VETTING_TRACKS, INTRO_STATES,
)


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
    leader = store.get_leader(leader_id)
    if not leader:
        return api_error("Leader not found", 404)
    return api_ok(leader)


# ── Supply side: apply / join ────────────────────────────────────────────────

@api_v1_bp.route('/marketplace/leaders', methods=['POST'])
@require_org()
def marketplace_create_leader():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    headline = (data.get("headline") or "").strip()
    if not name:
        return api_error("name is required", 400)
    track = data.get("track") or "ml_platform"
    if track not in VETTING_TRACKS:
        return api_error(f"track must be one of {', '.join(VETTING_TRACKS)}", 400)
    leader = store.create_leader(
        name=name, headline=headline, bio=data.get("bio", ""),
        location=data.get("location", ""), skills=data.get("skills") or [],
        sectors=data.get("sectors") or [], seniority=data.get("seniority", ""),
        track=track, engagement=data.get("engagement", "both"),
        comp_expectation=data.get("comp_expectation", ""),
        years_experience=int(data.get("years_experience") or 0),
        vetting_status="pending",
    )
    return api_ok(leader, 201)


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
    leader = store.get_leader(leader_id)
    if not leader:
        return api_error("Leader not found", 404)
    data = request.get_json() or {}
    responses = data.get("responses") or []
    if not isinstance(responses, list) or not responses:
        return api_error("responses must be a non-empty array", 400)

    track = data.get("track") or leader.get("track") or "ml_platform"
    from services.marketplace.vetting import score_vetting
    result = score_vetting(leader_name=leader["name"], track=track, responses=responses)
    vetting = result.to_dict()
    vetting["track"] = track

    updated = store.set_leader_vetting(leader_id, vetting, result.status)

    # Audit the AI decision (EU AI Act Art. 13 transparency) — reuse the existing
    # decision logger, same as console screening.
    try:
        from services.compliance.decision_logger import log_decision
        log_decision(
            org_id=ctx.org_id,
            decision_type="screening_score",
            candidate_id=leader_id,
            opportunity_id=None,
            inputs={"channel": "marketplace_vetting", "track": track,
                    "n_responses": len(responses)},
            model_used=result.model_used,
            score=round(result.score / 100.0, 2),
            explanation=result.rationale,
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


# ── Introductions (the billable event) ───────────────────────────────────────

@api_v1_bp.route('/marketplace/leaders/<leader_id>/introductions', methods=['POST'])
@require_org()
def marketplace_request_introduction(leader_id):
    ctx = get_org_context()
    leader = store.get_leader(leader_id)
    if not leader:
        return api_error("Leader not found", 404)
    if leader.get("vetting_status") != "verified":
        return api_error("Introductions can only be requested for vetted leaders", 400)

    data = request.get_json() or {}
    company = data.get("company") or {}
    if not company.get("name"):
        # Fall back to the buyer's org name if no explicit company supplied.
        company = {"name": data.get("company_name") or "Your company"}

    opp = None
    opportunity_id = data.get("opportunity_id")
    if opportunity_id:
        opp = store.get_opportunity(opportunity_id)

    fee_pct = data.get("placement_fee_pct")
    try:
        fee_pct = float(fee_pct) if fee_pct is not None else DEFAULT_PLACEMENT_FEE_PCT
    except (TypeError, ValueError):
        fee_pct = DEFAULT_PLACEMENT_FEE_PCT

    first_year_comp = data.get("first_year_comp")
    try:
        first_year_comp = float(first_year_comp) if first_year_comp not in (None, "") else None
    except (TypeError, ValueError):
        first_year_comp = None

    intro = store.create_introduction(
        org_id=ctx.org_id, actor_id=ctx.user_id,
        leader_id=leader_id, leader_name=leader["name"], company=company,
        opportunity_id=opportunity_id,
        opportunity_title=(opp or {}).get("title") if opp else data.get("opportunity_title"),
        message=data.get("message", ""), first_year_comp=first_year_comp,
        fee_pct=fee_pct, status="requested",
    )
    return api_ok(intro, 201)


@api_v1_bp.route('/marketplace/introductions', methods=['GET'])
@require_org()
def marketplace_list_introductions():
    # MVP: operator pipeline is marketplace-wide. ?scope=mine restricts to the
    # caller's org (their own requested intros).
    ctx = get_org_context()
    scope = request.args.get("scope")
    org_id = ctx.org_id if scope == "mine" else None
    intros = store.list_introductions(org_id=org_id)
    # Pipeline economics summary for the admin view.
    total_fees = sum(i["placement_fee_amount"] or 0 for i in intros if i["status"] == "hired")
    pipeline_fees = sum(i["placement_fee_amount"] or 0 for i in intros
                        if i["status"] in ("accepted", "interviewing") and i["placement_fee_amount"])
    return api_ok({
        "introductions": intros,
        "total": len(intros),
        "summary": {
            "hired": sum(1 for i in intros if i["status"] == "hired"),
            "open": sum(1 for i in intros if i["status"] in ("requested", "accepted", "interviewing")),
            "realised_fees": round(total_fees, 2),
            "pipeline_fees": round(pipeline_fees, 2),
        },
    })


@api_v1_bp.route('/marketplace/introductions/<intro_id>', methods=['PATCH'])
@require_org()
def marketplace_update_introduction(intro_id):
    data = request.get_json() or {}
    status = data.get("status")
    if status is not None and status not in INTRO_STATES:
        return api_error(f"status must be one of {', '.join(INTRO_STATES)}", 400)
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

    intro = store.update_introduction(
        intro_id, status=status, hired=hired,
        first_year_comp=first_year_comp, fee_pct=fee_pct,
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
