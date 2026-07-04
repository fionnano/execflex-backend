"""Matching engine endpoints — org-scoped."""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


@api_v1_bp.route('/matches', methods=['POST'])
@require_org()
def run_match():
    """Match candidates against a job. Returns ranked results with explanations."""
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    job_id = data.get("job_id")
    if not job_id:
        return api_error("job_id is required", 400)

    from config.clients import supabase_client

    job_result = supabase_client.table("opportunities") \
        .select("*") \
        .eq("id", job_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not job_result.data:
        return api_error("Job not found", 404)
    job_data = job_result.data[0]

    candidates_result = supabase_client.table("people_profiles") \
        .select("*") \
        .eq("organization_id", ctx.org_id) \
        .neq("pipeline_stage", "rejected") \
        .neq("pipeline_stage", "withdrawn") \
        .execute()

    from services.matching.models import Candidate, Role
    from services.matching.engine import MatchEngine

    role = Role(
        id=job_data["id"],
        title=job_data.get("title", ""),
        industry=job_data.get("industry", ""),
        required_skills=set(job_data.get("metadata", {}).get("required_skills", [])),
        min_experience=job_data.get("metadata", {}).get("min_experience", 0),
        location=job_data.get("location", ""),
        commitment_type=job_data.get("commitment_type", ""),
        budget_min=job_data.get("pay_range_min") or 0,
        budget_max=job_data.get("pay_range_max") or 0,
        is_ned=job_data.get("type") == "hire_ned",
        description=job_data.get("description", ""),
    )

    candidates = []
    for c in candidates_result.data:
        candidates.append(Candidate(
            id=c["id"],
            name=f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
            headline=c.get("headline", ""),
            industries=set(c.get("industries", []) or []),
            skills=set(c.get("source_metadata", {}).get("skills", []) if c.get("source_metadata") else []),
            experience_years=c.get("years_experience", 0) or 0,
            location=c.get("location", ""),
        ))

    engine = MatchEngine(weights=data.get("weights"))
    limit = data.get("limit", 20)
    min_score = data.get("min_score", 0.0)
    results = engine.match(candidates, role, limit=limit, min_score=min_score)

    from services.compliance.decision_logger import log_decision
    for r in results[:5]:
        log_decision(
            org_id=ctx.org_id,
            decision_type="match_rank",
            candidate_id=r.candidate.id,
            opportunity_id=job_id,
            score=r.score,
            explanation=r.explanation.summary,
            dimension_scores={k: {"score": v.score, "reason": v.reason}
                              for k, v in r.explanation.dimension_scores.items()},
        )

    return api_ok({
        "job_id": job_id,
        "job_title": job_data.get("title"),
        "match_count": len(results),
        "matches": [
            {
                "rank": r.rank,
                "candidate_id": r.candidate.id,
                "candidate_name": r.candidate.name,
                "score": r.score,
                "summary": r.explanation.summary,
                "dimensions": {
                    k: {"score": round(v.score, 1), "reason": v.reason}
                    for k, v in r.explanation.dimension_scores.items()
                },
            }
            for r in results
        ],
    })
