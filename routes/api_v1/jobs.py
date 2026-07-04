"""Job (opportunity) CRUD endpoints — org-scoped."""
from flask import request, jsonify
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


@api_v1_bp.route('/jobs', methods=['GET'])
@require_org()
def list_jobs():
    ctx = get_org_context()
    from config.clients import supabase_client
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    offset = (page - 1) * per_page

    query = supabase_client.table("opportunities") \
        .select("*", count="exact") \
        .eq("organization_id", ctx.org_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + per_page - 1)

    status = request.args.get('status')
    if status:
        query = query.eq("status", status)

    result = query.execute()
    return jsonify({
        "ok": True,
        "data": result.data,
        "pagination": {
            "total": result.count or 0,
            "page": page,
            "per_page": per_page,
        }
    }), 200


@api_v1_bp.route('/jobs', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def create_job():
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    title = data.get("title", "").strip()
    if not title:
        return api_error("title is required", 400)

    pay_min = data.get("pay_range_min")
    pay_max = data.get("pay_range_max")
    if pay_min is None or pay_max is None:
        return api_error("pay_range_min and pay_range_max are required (Pay Transparency Directive)", 400)

    from config.clients import supabase_client
    row = {
        "organization_id": ctx.org_id,
        "created_by_user_id": ctx.user_id,
        "title": title,
        "description": data.get("description", ""),
        "industry": data.get("industry", ""),
        "location": data.get("location", ""),
        "is_remote": data.get("is_remote", False),
        "commitment_type": data.get("commitment_type", ""),
        "compensation": data.get("compensation", ""),
        "status": "open",
        "metadata": data.get("metadata", {}),
        "pay_range_min": pay_min,
        "pay_range_max": pay_max,
        "pay_range_currency": data.get("pay_range_currency", "EUR"),
        "pay_range_period": data.get("pay_range_period", "annual"),
    }
    result = supabase_client.table("opportunities").insert(row).execute()

    from services.compliance.decision_logger import log_activity
    log_activity(ctx.org_id, "job", result.data[0]["id"], "job_created",
                 ctx.user_id, f"Job posted: {title}")

    return api_ok(result.data[0], 201)


@api_v1_bp.route('/jobs/<job_id>', methods=['GET'])
@require_org()
def get_job(job_id):
    ctx = get_org_context()
    from config.clients import supabase_client
    result = supabase_client.table("opportunities") \
        .select("*") \
        .eq("id", job_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()

    if not result.data:
        return api_error("Job not found", 404)
    return api_ok(result.data[0])


@api_v1_bp.route('/jobs/<job_id>', methods=['PATCH'])
@require_org(allowed_roles=["owner", "recruiter"])
def update_job(job_id):
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    allowed_fields = {
        "title", "description", "industry", "location", "is_remote",
        "commitment_type", "compensation", "status", "metadata",
        "pay_range_min", "pay_range_max", "pay_range_currency", "pay_range_period"
    }
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if not updates:
        return api_error("No valid fields to update", 400)

    from config.clients import supabase_client
    result = supabase_client.table("opportunities") \
        .update(updates) \
        .eq("id", job_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()

    if not result.data:
        return api_error("Job not found", 404)
    return api_ok(result.data[0])
