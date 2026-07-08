"""Candidate CRUD endpoints — org-scoped."""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


def _serialize_candidate(row: dict) -> dict:
    """Shape a people_profiles row for the console.

    The console expects full_name / email / phone / experience_years / skills;
    people_profiles stores first_name+last_name, years_experience, and contact
    details inside source_metadata (upload_email / upload_phone). Raw columns
    are preserved alongside the computed fields.
    """
    sm = row.get("source_metadata") or {}
    industries = row.get("industries") or []
    out = dict(row)
    out["full_name"] = (
        f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
        or row.get("headline")
        or "Unnamed candidate"
    )
    out["email"] = row.get("email") or sm.get("upload_email") or sm.get("email") or ""
    out["phone"] = row.get("phone") or sm.get("upload_phone") or sm.get("phone") or ""
    out["experience_years"] = row.get("years_experience") or 0
    out["skills"] = row.get("skills") or sm.get("skills") or []
    out["industry"] = row.get("industry") or (industries[0] if isinstance(industries, list) and industries else "")
    out.setdefault("pipeline_stage", "sourced")
    return out


def _split_full_name(full_name: str) -> tuple:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


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
        "candidates": [_serialize_candidate(r) for r in (result.data or [])],
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
    return api_ok(_serialize_candidate(result.data[0]))


@api_v1_bp.route('/candidates', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def create_candidate():
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    if not first_name and data.get("full_name"):
        first_name, last_name = _split_full_name(data["full_name"])
    if not (first_name or last_name):
        return api_error("full_name or first_name is required", 400)

    source_metadata = {}
    if data.get("email"):
        source_metadata["upload_email"] = data["email"].strip()
    if data.get("phone"):
        source_metadata["upload_phone"] = data["phone"].strip()
    if data.get("skills"):
        source_metadata["skills"] = data["skills"]

    from config.clients import supabase_client
    row = {
        "organization_id": ctx.org_id,
        "first_name": first_name,
        "last_name": last_name,
        "headline": data.get("headline", ""),
        "location": data.get("location", ""),
        "years_experience": data.get("years_experience", data.get("experience_years", 0)),
        "industries": data.get("industries", [data["industry"]] if data.get("industry") else []),
        "source": data.get("source", "manual"),
        "source_metadata": source_metadata,
        "pipeline_stage": "sourced",
    }
    result = supabase_client.table("people_profiles").insert(row).execute()

    from services.compliance.decision_logger import log_activity
    log_activity(ctx.org_id, "candidate", result.data[0]["id"], "candidate_created",
                 ctx.user_id, f"Candidate added: {first_name} {last_name}")

    return api_ok(_serialize_candidate(result.data[0]), 201)


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
    if data.get("full_name") and "first_name" not in updates:
        updates["first_name"], updates["last_name"] = _split_full_name(data["full_name"])
    if data.get("experience_years") is not None and "years_experience" not in updates:
        updates["years_experience"] = data["experience_years"]

    from config.clients import supabase_client

    # Contact details live in source_metadata — merge, don't clobber.
    if data.get("email") or data.get("phone"):
        existing = supabase_client.table("people_profiles") \
            .select("source_metadata") \
            .eq("id", candidate_id) \
            .eq("organization_id", ctx.org_id) \
            .execute()
        if not existing.data:
            return api_error("Candidate not found", 404)
        sm = dict(existing.data[0].get("source_metadata") or {})
        if data.get("email"):
            sm["upload_email"] = data["email"].strip()
        if data.get("phone"):
            sm["upload_phone"] = data["phone"].strip()
        updates["source_metadata"] = sm

    if not updates:
        return api_error("No updatable fields provided", 400)

    result = supabase_client.table("people_profiles") \
        .update(updates) \
        .eq("id", candidate_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not result.data:
        return api_error("Candidate not found", 404)
    return api_ok(_serialize_candidate(result.data[0]))
