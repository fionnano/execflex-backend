"""Pipeline management endpoints — org-scoped."""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error

VALID_STAGES = {'sourced', 'screened', 'shortlisted', 'interviewing',
                'offered', 'placed', 'rejected', 'withdrawn'}


@api_v1_bp.route('/pipeline', methods=['GET'])
@require_org()
def get_pipeline():
    """Get pipeline board: candidates grouped by stage, optionally filtered by job."""
    ctx = get_org_context()
    from config.clients import supabase_client

    job_id = request.args.get('job_id')

    query = supabase_client.table("people_profiles") \
        .select("id, first_name, last_name, headline, pipeline_stage, stage_changed_at") \
        .eq("organization_id", ctx.org_id)

    if job_id:
        app_result = supabase_client.table("applications") \
            .select("candidate_id") \
            .eq("organization_id", ctx.org_id) \
            .eq("opportunity_id", job_id) \
            .execute()
        candidate_ids = [a["candidate_id"] for a in app_result.data]
        if not candidate_ids:
            return api_ok({stage: [] for stage in VALID_STAGES})
        query = query.in_("id", candidate_ids)

    result = query.execute()

    pipeline = {stage: [] for stage in VALID_STAGES}
    for c in result.data:
        stage = c.get("pipeline_stage", "sourced") or "sourced"
        if stage in pipeline:
            pipeline[stage].append(c)

    return api_ok(pipeline)


@api_v1_bp.route('/pipeline/move', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def move_stage():
    """Move a candidate to a new pipeline stage."""
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    candidate_id = data.get("candidate_id")
    to_stage = data.get("to_stage")
    reason = data.get("reason", "")
    opportunity_id = data.get("opportunity_id")

    if not candidate_id or not to_stage:
        return api_error("candidate_id and to_stage required", 400)
    if to_stage not in VALID_STAGES:
        return api_error(f"Invalid stage. Must be one of: {', '.join(sorted(VALID_STAGES))}", 400)

    terminal = {'rejected', 'withdrawn'}
    if to_stage in terminal:
        from services.compliance.human_review import require_human_review_for_reject
        check = require_human_review_for_reject(ctx, candidate_id, reason)
        if not check["allowed"]:
            return api_error(check["message"], 422)

    from config.clients import supabase_client
    from datetime import datetime, timezone

    current = supabase_client.table("people_profiles") \
        .select("pipeline_stage") \
        .eq("id", candidate_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not current.data:
        return api_error("Candidate not found", 404)

    from_stage = current.data[0].get("pipeline_stage", "sourced")
    now = datetime.now(timezone.utc).isoformat()

    supabase_client.table("people_profiles") \
        .update({"pipeline_stage": to_stage, "stage_changed_at": now, "stage_changed_by": ctx.user_id}) \
        .eq("id", candidate_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()

    supabase_client.table("pipeline_events").insert({
        "organization_id": ctx.org_id,
        "candidate_id": candidate_id,
        "opportunity_id": opportunity_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "changed_by": ctx.user_id,
        "reason": reason,
    }).execute()

    from services.compliance.decision_logger import log_decision, log_activity
    if to_stage in terminal:
        log_decision(
            org_id=ctx.org_id,
            decision_type="reject" if to_stage == "rejected" else "stage_change",
            candidate_id=candidate_id,
            opportunity_id=opportunity_id,
            inputs={"from_stage": from_stage, "to_stage": to_stage},
            explanation=reason,
            human_reviewed=True,
            human_reviewer_id=ctx.user_id,
        )

    log_activity(ctx.org_id, "candidate", candidate_id, "pipeline_move",
                 ctx.user_id, f"{from_stage} → {to_stage}" + (f": {reason}" if reason else ""))

    return api_ok({"candidate_id": candidate_id, "from_stage": from_stage, "to_stage": to_stage})


@api_v1_bp.route('/pipeline/events', methods=['GET'])
@require_org()
def pipeline_events():
    ctx = get_org_context()
    from config.clients import supabase_client

    candidate_id = request.args.get('candidate_id')
    limit = min(request.args.get('limit', 50, type=int), 200)

    query = supabase_client.table("pipeline_events") \
        .select("*") \
        .eq("organization_id", ctx.org_id) \
        .order("created_at", desc=True) \
        .limit(limit)

    if candidate_id:
        query = query.eq("candidate_id", candidate_id)

    result = query.execute()
    return api_ok(result.data)
