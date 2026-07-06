"""
AI Decision Logger — EU AI Act Art. 13 compliance.

Every AI-influenced decision (screening score, match rank, stage change,
reject) is logged with: inputs, model, score, explanation, human review status.
"""
from typing import Any, Dict, Optional


def log_decision(
    org_id: str,
    decision_type: str,
    candidate_id: Optional[str] = None,
    opportunity_id: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    model_used: Optional[str] = None,
    model_version: Optional[str] = None,
    score: Optional[float] = None,
    explanation: Optional[str] = None,
    dimension_scores: Optional[Dict] = None,
    human_reviewed: bool = False,
    human_reviewer_id: Optional[str] = None,
) -> Optional[str]:
    """Log an AI decision. Returns the decision log ID."""
    try:
        from config.clients import supabase_client
        row = {
            "organization_id": org_id,
            "decision_type": decision_type,
            "candidate_id": candidate_id,
            "opportunity_id": opportunity_id,
            "inputs": inputs or {},
            "model_used": model_used,
            "model_version": model_version,
            "score": float(score) if score is not None else None,
            "explanation": explanation,
            "dimension_scores": dimension_scores,
            "human_reviewed": human_reviewed,
            "human_reviewer_id": human_reviewer_id,
        }
        if human_reviewed and human_reviewer_id:
            from datetime import datetime, timezone
            row["human_review_at"] = datetime.now(timezone.utc).isoformat()

        result = supabase_client.table("ai_decision_log").insert(row).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as e:
        print(f"[DecisionLogger] Error logging decision: {e}", flush=True)
        return None


def log_activity(
    org_id: str,
    entity_type: str,
    entity_id: Optional[str],
    activity_type: str,
    actor_id: Optional[str] = None,
    summary: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> None:
    """Log a CRM activity event."""
    try:
        from config.clients import supabase_client
        supabase_client.table("activity_log").insert({
            "organization_id": org_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "activity_type": activity_type,
            "actor_id": actor_id,
            "summary": summary,
            "metadata": metadata or {},
        }).execute()
    except Exception as e:
        print(f"[ActivityLog] Error logging activity: {e}", flush=True)
