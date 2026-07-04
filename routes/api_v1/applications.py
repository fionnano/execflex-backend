"""Application (candidate→job link) endpoints — org-scoped."""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


@api_v1_bp.route('/applications', methods=['GET'])
@require_org()
def list_applications():
    ctx = get_org_context()
    from config.clients import supabase_client
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    offset = (page - 1) * per_page

    query = supabase_client.table("applications") \
        .select("*", count="exact") \
        .eq("organization_id", ctx.org_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + per_page - 1)

    job_id = request.args.get('job_id')
    if job_id:
        query = query.eq("opportunity_id", job_id)

    status = request.args.get('status')
    if status:
        query = query.eq("status", status)

    result = query.execute()
    return api_ok({
        "applications": result.data,
        "total": result.count or 0,
        "page": page,
    })


@api_v1_bp.route('/applications', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def create_application():
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    candidate_id = data.get("candidate_id")
    opportunity_id = data.get("opportunity_id")
    if not candidate_id or not opportunity_id:
        return api_error("candidate_id and opportunity_id required", 400)

    from config.clients import supabase_client
    row = {
        "organization_id": ctx.org_id,
        "candidate_id": candidate_id,
        "opportunity_id": opportunity_id,
        "source": data.get("source", "direct"),
        "status": "applied",
    }
    try:
        result = supabase_client.table("applications").insert(row).execute()
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return api_error("Application already exists for this candidate and job", 409)
        raise

    from services.compliance.decision_logger import log_activity
    log_activity(ctx.org_id, "candidate", candidate_id, "application_created",
                 ctx.user_id, f"Applied to job {opportunity_id}")

    return api_ok(result.data[0], 201)


@api_v1_bp.route('/applications/<app_id>/status', methods=['PATCH'])
@require_org(allowed_roles=["owner", "recruiter"])
def update_application_status(app_id):
    ctx = get_org_context()
    data = request.get_json()
    new_status = data.get("status") if data else None
    if not new_status:
        return api_error("status is required", 400)

    valid = {'applied', 'screening', 'screened', 'shortlisted',
             'interviewing', 'offered', 'placed', 'rejected', 'withdrawn'}
    if new_status not in valid:
        return api_error(f"Invalid status. Must be one of: {', '.join(sorted(valid))}", 400)

    terminal_statuses = {'rejected', 'withdrawn'}
    if new_status in terminal_statuses:
        from services.compliance.human_review import require_human_review_for_reject
        check = require_human_review_for_reject(ctx, app_id, data.get("reason", ""))
        if not check["allowed"]:
            return api_error(check["message"], 422)

    from config.clients import supabase_client
    result = supabase_client.table("applications") \
        .update({"status": new_status, "notes": data.get("reason", "")}) \
        .eq("id", app_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()

    if not result.data:
        return api_error("Application not found", 404)

    from services.compliance.decision_logger import log_decision, log_activity
    if new_status in terminal_statuses:
        log_decision(
            org_id=ctx.org_id,
            decision_type="reject" if new_status == "rejected" else "stage_change",
            candidate_id=result.data[0]["candidate_id"],
            opportunity_id=result.data[0]["opportunity_id"],
            inputs={"previous_status": "unknown", "new_status": new_status},
            explanation=data.get("reason", ""),
            human_reviewed=True,
            human_reviewer_id=ctx.user_id,
        )

    log_activity(ctx.org_id, "candidate", result.data[0]["candidate_id"],
                 f"status_changed_to_{new_status}", ctx.user_id,
                 f"Application status → {new_status}")

    return api_ok(result.data[0])
