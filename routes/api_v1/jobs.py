"""Job (opportunity) CRUD endpoints — org-scoped.

Persistence notes (see DECISIONS.md D-21):
- opportunities.type is a NOT-NULL enum (hire_fractional | hire_ned). The console
  posts general roles → default hire_fractional (override via `type`/`is_ned`).
- opportunities.commitment_type is an enum (fractional | full_time | part_time |
  contract). Client display values ("full-time", "interim") are normalised here.
- The matching engine (routes/api_v1/matches.py) reads a job's required skills from
  metadata.required_skills and min experience from metadata.min_experience, so the
  console's skills_required / experience fields are routed there.
"""
from flask import request, jsonify
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


# ── Enum reconciliation + skills routing ─────────────────────────────────────

_VALID_TYPE = {"hire_fractional", "hire_ned"}
_VALID_COMMITMENT = {"fractional", "full_time", "part_time", "contract"}
_COMMITMENT_ALIASES = {
    "full-time": "full_time", "fulltime": "full_time", "full_time": "full_time",
    "part-time": "part_time", "parttime": "part_time", "part_time": "part_time",
    "contract": "contract", "interim": "contract", "temporary": "contract",
    "fractional": "fractional",
}


def _normalize_commitment(value) -> str:
    if not value:
        return "full_time"
    v = str(value).strip().lower()
    return _COMMITMENT_ALIASES.get(v, "full_time" if v not in _VALID_COMMITMENT else v)


def _resolve_type(data: dict) -> str:
    t = str(data.get("type") or "").strip().lower()
    if t in _VALID_TYPE:
        return t
    if data.get("is_ned") or str(data.get("role_type") or "").lower() in ("ned", "hire_ned"):
        return "hire_ned"
    return "hire_fractional"


def _extract_skills(data: dict) -> list:
    for key in ("skills_required", "required_skills", "skills"):
        v = data.get(key)
        if v:
            if isinstance(v, str):
                return [s.strip() for s in v.split(",") if s.strip()]
            if isinstance(v, (list, tuple)):
                return [str(s).strip() for s in v if str(s).strip()]
    return []


def _coerce_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _build_metadata(data: dict) -> dict:
    """Merge client metadata with the fields the matcher reads."""
    meta = dict(data.get("metadata") or {})
    skills = _extract_skills(data)
    if skills:
        meta["required_skills"] = skills
    exp_min = _coerce_int(data.get("experience_min", data.get("min_experience")))
    if exp_min is not None:
        meta["min_experience"] = exp_min
    exp_max = _coerce_int(data.get("experience_max", data.get("max_experience")))
    if exp_max is not None:
        meta["max_experience"] = exp_max
    return meta


def _serialize_job(row: dict) -> dict:
    """Surface metadata-stored skills/experience back at the top level so the
    edit form and any consumer round-trips them."""
    meta = row.get("metadata") or {}
    out = dict(row)
    out["skills_required"] = meta.get("required_skills", [])
    out["experience_min"] = meta.get("min_experience")
    out["experience_max"] = meta.get("max_experience")
    return out


# ── Endpoints ────────────────────────────────────────────────────────────────

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
        "data": [_serialize_job(r) for r in (result.data or [])],
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
        # type is a NOT-NULL enum — always populate it (this was the 500).
        "type": _resolve_type(data),
        "title": title,
        "description": data.get("description", ""),
        "industry": data.get("industry", ""),
        "location": data.get("location", ""),
        "is_remote": data.get("is_remote", False),
        "commitment_type": _normalize_commitment(data.get("commitment_type")),
        "compensation": data.get("compensation", ""),
        "status": "open",
        # Route skills_required / experience into metadata where the matcher reads.
        "metadata": _build_metadata(data),
        "pay_range_min": pay_min,
        "pay_range_max": pay_max,
        "pay_range_currency": data.get("pay_range_currency", "EUR"),
        "pay_range_period": data.get("pay_range_period", "annual"),
    }
    result = supabase_client.table("opportunities").insert(row).execute()

    from services.compliance.decision_logger import log_activity
    log_activity(ctx.org_id, "job", result.data[0]["id"], "job_created",
                 ctx.user_id, f"Job posted: {title}")

    return api_ok(_serialize_job(result.data[0]), 201)


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
    return api_ok(_serialize_job(result.data[0]))


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
        "pay_range_min", "pay_range_max", "pay_range_currency", "pay_range_period",
        "type",
    }
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if "commitment_type" in updates:
        updates["commitment_type"] = _normalize_commitment(updates["commitment_type"])
    if "type" in updates and str(updates["type"]).lower() not in _VALID_TYPE:
        updates.pop("type")
    # Route edited skills/experience into metadata (same as create).
    if any(k in data for k in ("skills_required", "required_skills", "skills",
                               "experience_min", "experience_max")):
        from config.clients import supabase_client as _sb
        existing = _sb.table("opportunities").select("metadata") \
            .eq("id", job_id).eq("organization_id", ctx.org_id).execute()
        base_meta = (existing.data[0].get("metadata") if existing.data else {}) or {}
        merged = {**base_meta, **(updates.get("metadata") or {})}
        merged_data = {"metadata": merged, **data}
        updates["metadata"] = _build_metadata(merged_data)
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
    return api_ok(_serialize_job(result.data[0]))
