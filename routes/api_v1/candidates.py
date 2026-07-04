"""Candidate CRUD endpoints — org-scoped."""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


@api_v1_bp.route('/candidates', methods=['GET'])
@require_org()
def list_candidates():
    ctx = get_org_context()
    from config.clients import supabase_client
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    offset = (page - 1) * per_page

    query = supabase_client.table("people_profiles") \
        .select("*", count="exact") \
        .eq("organization_id", ctx.org_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + per_page - 1)

    stage = request.args.get('pipeline_stage')
    if stage:
        query = query.eq("pipeline_stage", stage)

    search = request.args.get('q')
    if search:
        query = query.or_(f"first_name.ilike.%{search}%,last_name.ilike.%{search}%,headline.ilike.%{search}%")

    result = query.execute()
    return api_ok({
        "candidates": result.data,
        "total": result.count or 0,
        "page": page,
        "per_page": per_page,
    })


@api_v1_bp.route('/candidates/<candidate_id>', methods=['GET'])
@require_org()
def get_candidate(candidate_id):
    ctx = get_org_context()
    from config.clients import supabase_client
    result = supabase_client.table("people_profiles") \
        .select("*") \
        .eq("id", candidate_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not result.data:
        return api_error("Candidate not found", 404)
    return api_ok(result.data[0])


@api_v1_bp.route('/candidates', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def create_candidate():
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    from config.clients import supabase_client
    row = {
        "organization_id": ctx.org_id,
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "headline": data.get("headline", ""),
        "location": data.get("location", ""),
        "years_experience": data.get("years_experience", 0),
        "industries": data.get("industries", []),
        "source": data.get("source", "manual"),
        "pipeline_stage": "sourced",
    }
    result = supabase_client.table("people_profiles").insert(row).execute()

    from services.compliance.decision_logger import log_activity
    log_activity(ctx.org_id, "candidate", result.data[0]["id"], "candidate_created",
                 ctx.user_id, f"Candidate added: {row['first_name']} {row['last_name']}")

    return api_ok(result.data[0], 201)


@api_v1_bp.route('/candidates/<candidate_id>', methods=['PATCH'])
@require_org(allowed_roles=["owner", "recruiter"])
def update_candidate(candidate_id):
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    allowed_fields = {
        "first_name", "last_name", "headline", "location",
        "years_experience", "industries", "approved"
    }
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    from config.clients import supabase_client
    result = supabase_client.table("people_profiles") \
        .update(updates) \
        .eq("id", candidate_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not result.data:
        return api_error("Candidate not found", 404)
    return api_ok(result.data[0])
