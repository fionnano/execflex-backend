"""Compliance endpoints — decision log viewer, data rights, AI notice."""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


@api_v1_bp.route('/compliance/decisions', methods=['GET'])
@require_org()
def list_decisions():
    """View the AI decision log — Art. 13 transparency surface."""
    ctx = get_org_context()
    from config.clients import supabase_client
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    offset = (page - 1) * per_page

    query = supabase_client.table("ai_decision_log") \
        .select("*", count="exact") \
        .eq("organization_id", ctx.org_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + per_page - 1)

    decision_type = request.args.get('type')
    if decision_type:
        # The console filters by family ("screening", "matching"); rows are
        # logged with specific types ("screening_score", "match_rank") —
        # prefix-match so family filters actually return their rows.
        query = query.like("decision_type", f"{decision_type}%")

    unreviewed = request.args.get('unreviewed')
    if unreviewed == 'true':
        query = query.eq("human_reviewed", False)

    candidate_id = request.args.get('candidate_id')
    if candidate_id:
        query = query.eq("candidate_id", candidate_id)

    result = query.execute()
    return api_ok({
        "decisions": result.data,
        "total": result.count or 0,
        "page": page,
    })


@api_v1_bp.route('/compliance/decisions/<decision_id>/review', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def review_decision(decision_id):
    """Mark a decision as human-reviewed (approve or override)."""
    ctx = get_org_context()
    data = request.get_json()
    override = data.get("override", False) if data else False
    override_reason = data.get("reason", "") if data else ""

    from config.clients import supabase_client
    from datetime import datetime, timezone

    updates = {
        "human_reviewed": True,
        "human_reviewer_id": ctx.user_id,
        "human_review_at": datetime.now(timezone.utc).isoformat(),
        "human_override": override,
    }
    if override:
        if not override_reason:
            return api_error("reason required for overrides", 400)
        updates["override_reason"] = override_reason

    result = supabase_client.table("ai_decision_log") \
        .update(updates) \
        .eq("id", decision_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()

    if not result.data:
        return api_error("Decision not found", 404)
    return api_ok(result.data[0])


@api_v1_bp.route('/compliance/data-rights', methods=['GET'])
@require_org()
def list_data_rights():
    ctx = get_org_context()
    from config.clients import supabase_client
    result = supabase_client.table("data_rights_requests") \
        .select("*") \
        .eq("organization_id", ctx.org_id) \
        .order("created_at", desc=True) \
        .execute()
    return api_ok(result.data)


@api_v1_bp.route('/compliance/data-rights', methods=['POST'])
def create_data_rights_request():
    """Public endpoint — candidates can submit data rights requests without auth."""
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    email = data.get("email", "").strip()
    request_type = data.get("type", "access")
    if not email:
        return api_error("email is required", 400)

    org_id = data.get("organization_id")
    if not org_id:
        return api_error("organization_id is required", 400)

    from services.compliance.data_rights import create_data_rights_request as create_req
    try:
        result = create_req(
            org_id=org_id,
            request_type=request_type,
            requester_email=email,
            requester_name=data.get("name", ""),
        )
        return api_ok({"request_id": result.get("id"), "status": "pending"}, 201)
    except ValueError as e:
        return api_error(str(e), 400)


@api_v1_bp.route('/compliance/data-rights/<request_id>', methods=['PATCH'])
@require_org(allowed_roles=["owner"])
def update_data_rights(request_id):
    ctx = get_org_context()
    data = request.get_json()
    new_status = data.get("status") if data else None
    if not new_status:
        return api_error("status required", 400)

    from services.compliance.data_rights import process_data_rights_request
    try:
        result = process_data_rights_request(
            org_id=ctx.org_id,
            request_id=request_id,
            new_status=new_status,
            completed_by=ctx.user_id,
            notes=data.get("notes", ""),
        )
        return api_ok(result)
    except ValueError as e:
        return api_error(str(e), 400)


@api_v1_bp.route('/compliance/ai-notice', methods=['GET'])
def ai_notice():
    """Public endpoint — Art. 50 EU AI Act: transparency notice about AI use."""
    return api_ok({
        "notice": (
            "ExecFlex uses artificial intelligence in the following ways:\n\n"
            "1. VOICE SCREENING: An AI assistant conducts initial screening calls with candidates. "
            "Candidates are informed at the start of each call and must consent before proceeding.\n\n"
            "2. CANDIDATE MATCHING: An AI-powered matching engine scores candidates against job "
            "requirements across multiple dimensions (skills, experience, location, etc.). "
            "All scores include human-readable explanations.\n\n"
            "3. SCORING: Screening responses are scored to generate recommendations. "
            "No candidate is automatically rejected — all terminal decisions require human review.\n\n"
            "CANDIDATE RIGHTS:\n"
            "- Right to know AI is being used (this notice)\n"
            "- Right to human review of any AI-influenced decision\n"
            "- Right to explanation of how scores were calculated\n"
            "- Right to access your data (GDPR Art. 15)\n"
            "- Right to erasure of your data (GDPR Art. 17)\n\n"
            "To exercise these rights, submit a request via the data rights endpoint "
            "or email privacy@execflex.ai."
        ),
        "version": "1.0",
        "last_updated": "2026-07-04",
    })
