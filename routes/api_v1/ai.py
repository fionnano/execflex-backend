"""AI agent endpoints — org-scoped.

These endpoints expose agentic-core recruitment agents behind feature flags.
When flags are off, endpoints return 404 (feature not enabled).
All AI-generated content is marked with ai_generated: true for EU AI Act
Art. 50 transparency compliance.
"""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


@api_v1_bp.route('/ai/status', methods=['GET'])
@require_org()
def ai_status():
    """Return which AI features are enabled."""
    from services.ai.feature_flags import get_flags_status
    return api_ok({"flags": get_flags_status()})


@api_v1_bp.route('/ai/generate-jd', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def generate_jd():
    """Generate a job description using LLM.

    Requires EXECFLEX_AI_JD_GENERATOR=1.
    Pay range is mandatory (EU Pay Transparency Directive).
    Output includes gender-neutral language check.
    """
    from services.ai.feature_flags import jd_generator_enabled
    if not jd_generator_enabled():
        return api_error("JD generator not enabled", 404)

    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    required = ["role_title", "responsibilities", "requirements",
                 "pay_range_min", "pay_range_max", "location"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return api_error(f"Missing required fields: {', '.join(missing)}", 400)

    from services.ai.agent_service import generate_jd as run_jd
    result = run_jd(
        role_title=data["role_title"],
        company_summary=data.get("company_summary", ""),
        responsibilities=data["responsibilities"],
        requirements=data["requirements"],
        pay_range_min=float(data["pay_range_min"]),
        pay_range_max=float(data["pay_range_max"]),
        pay_currency=data.get("pay_currency", "EUR"),
        location=data["location"],
        benefits=data.get("benefits"),
        team_context=data.get("team_context"),
        experience_years=data.get("experience_years"),
        industry=data.get("industry"),
        commitment_type=data.get("commitment_type"),
        remote_policy=data.get("remote_policy"),
    )

    if result is None:
        return api_error("JD generation failed — check logs", 500)

    from services.compliance.decision_logger import log_decision
    log_decision(
        org_id=ctx.org_id,
        decision_type="ai_jd_generation",
        model_used="claude-sonnet-4-5",
        score=0,
        explanation=f"JD generated for {data['role_title']}",
        inputs={"role_title": data["role_title"]},
    )

    return api_ok(result, 201)


@api_v1_bp.route('/ai/parse-cv', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def parse_cv():
    """Parse CV text into structured profile using LLM.

    Requires EXECFLEX_AI_CV_PARSER=1.
    Uses Haiku (cheap extraction tier).
    """
    from services.ai.feature_flags import cv_parser_enabled
    if not cv_parser_enabled():
        return api_error("CV parser not enabled", 404)

    ctx = get_org_context()
    data = request.get_json()
    if not data or not data.get("cv_text"):
        return api_error("cv_text is required", 400)

    from services.ai.agent_service import parse_cv as run_cv
    result = run_cv(
        cv_text=data["cv_text"],
        target_role=data.get("target_role"),
    )

    if result is None:
        return api_error("CV parsing failed — check logs", 500)

    from services.compliance.decision_logger import log_decision
    log_decision(
        org_id=ctx.org_id,
        decision_type="ai_cv_parse",
        model_used="claude-haiku-4-5",
        score=0,
        explanation=f"CV parsed: {result['profile'].get('full_name', 'unknown')}",
    )

    return api_ok(result)


@api_v1_bp.route('/ai/question-flow/<role_type>', methods=['GET'])
@require_org()
def question_flow(role_type):
    """Get per-role screening question flow.

    Requires EXECFLEX_AI_QUESTION_FLOW=1.
    """
    from services.ai.feature_flags import question_flow_enabled
    if not question_flow_enabled():
        return api_error("Question flow not enabled", 404)

    from services.ai.agent_service import get_question_flow_data
    flow = get_question_flow_data(role_type)
    if flow is None:
        return api_error("Question flow lookup failed", 500)

    return api_ok(flow)


@api_v1_bp.route('/ai/question-flow', methods=['GET'])
@require_org()
def list_question_flows():
    """List available role types for question flows."""
    from services.ai.feature_flags import question_flow_enabled
    if not question_flow_enabled():
        return api_error("Question flow not enabled", 404)

    try:
        from agentic_core.agents.recruitment import list_role_types
        return api_ok({"role_types": list_role_types()})
    except ImportError:
        return api_error("agentic-core not available", 500)


@api_v1_bp.route('/ai/compliance/snapshot', methods=['POST'])
@require_org()
def compliance_snapshot():
    """Run an EU AI Act snapshot self-assessment.

    Requires EXECFLEX_AI_COMPLIANCE_CHECK=1.
    Returns deterministic risk score + LLM-generated gap statements.
    Output includes ai_generated: true for EU AI Act Art. 50 transparency.
    """
    from services.ai.feature_flags import compliance_check_enabled
    if not compliance_check_enabled():
        return api_error("Compliance check not enabled", 404)

    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    uses_ai = data.get("uses_ai")
    if not uses_ai:
        return api_error("uses_ai is required", 400)

    kwargs = {
        "uses_ai": uses_ai,
        "business_functions": data.get("business_functions"),
        "affects_people": data.get("affects_people", "no"),
        "in_eu": data.get("in_eu", "no"),
        "has_documentation": data.get("has_documentation", "no"),
    }

    from services.ai.agent_service import snapshot_score, snapshot_gaps
    score_result = snapshot_score(**kwargs)
    if score_result is None:
        return api_error("Snapshot scoring failed — check logs", 500)

    gaps_result = snapshot_gaps(**kwargs)

    return api_ok({
        "score": score_result,
        "gaps": gaps_result,
        "ai_generated": True,
    })


@api_v1_bp.route('/ai/compliance/prohibited-check', methods=['POST'])
@require_org()
def compliance_prohibited_check():
    """Check answers against EU AI Act Article 5 prohibited practices.

    Requires EXECFLEX_AI_COMPLIANCE_CHECK=1.
    Pure logic — no LLM call. Returns hard-stop, prohibited, and
    high-risk flags with article references.
    """
    from services.ai.feature_flags import compliance_check_enabled
    if not compliance_check_enabled():
        return api_error("Compliance check not enabled", 404)

    data = request.get_json()
    if not data or not data.get("answers"):
        return api_error("answers object is required", 400)

    from services.ai.agent_service import check_prohibited_practices
    result = check_prohibited_practices(data["answers"])
    if result is None:
        return api_error("Prohibited practices check failed — check logs", 500)

    return api_ok(result)
