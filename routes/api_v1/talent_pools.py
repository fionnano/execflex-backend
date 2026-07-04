"""Talent pool endpoints — org-scoped. Design + scaffold only."""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


@api_v1_bp.route('/talent-pools', methods=['GET'])
@require_org()
def list_pools():
    ctx = get_org_context()
    from config.clients import supabase_client
    result = supabase_client.table("talent_pools") \
        .select("*") \
        .eq("organization_id", ctx.org_id) \
        .order("created_at", desc=True) \
        .execute()
    return api_ok(result.data)


@api_v1_bp.route('/talent-pools', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def create_pool():
    ctx = get_org_context()
    data = request.get_json()
    if not data or not data.get("name"):
        return api_error("name is required", 400)

    from config.clients import supabase_client
    result = supabase_client.table("talent_pools").insert({
        "organization_id": ctx.org_id,
        "name": data["name"],
        "description": data.get("description", ""),
        "criteria": data.get("criteria", {}),
        "is_verified": data.get("is_verified", False),
        "verification_method": data.get("verification_method"),
    }).execute()

    return api_ok(result.data[0], 201)


@api_v1_bp.route('/talent-pools/<pool_id>/members', methods=['GET'])
@require_org()
def list_pool_members(pool_id):
    ctx = get_org_context()
    from config.clients import supabase_client

    pool = supabase_client.table("talent_pools") \
        .select("id") \
        .eq("id", pool_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not pool.data:
        return api_error("Pool not found", 404)

    result = supabase_client.table("talent_pool_members") \
        .select("*") \
        .eq("pool_id", pool_id) \
        .execute()
    return api_ok(result.data)


@api_v1_bp.route('/talent-pools/<pool_id>/members', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def add_pool_member(pool_id):
    ctx = get_org_context()
    data = request.get_json()
    candidate_id = data.get("candidate_id") if data else None
    if not candidate_id:
        return api_error("candidate_id required", 400)

    from config.clients import supabase_client

    pool = supabase_client.table("talent_pools") \
        .select("id, organization_id") \
        .eq("id", pool_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not pool.data:
        return api_error("Pool not found", 404)

    try:
        result = supabase_client.table("talent_pool_members").insert({
            "pool_id": pool_id,
            "candidate_id": candidate_id,
        }).execute()
    except Exception as e:
        if "duplicate" in str(e).lower():
            return api_error("Candidate already in pool", 409)
        raise

    return api_ok(result.data[0], 201)
