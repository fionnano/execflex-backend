"""
Data rights request handling — GDPR Art. 15/17.

Candidate data access and erasure request intake and processing.
"""
from typing import Dict, Optional


def create_data_rights_request(
    org_id: str,
    request_type: str,
    requester_email: str,
    requester_name: str = "",
    candidate_id: Optional[str] = None,
) -> Dict:
    """Create a new data rights request. Returns the request record."""
    valid_types = {"access", "erasure", "rectification", "portability"}
    if request_type not in valid_types:
        raise ValueError(f"request_type must be one of: {', '.join(valid_types)}")

    from config.clients import supabase_client
    result = supabase_client.table("data_rights_requests").insert({
        "organization_id": org_id,
        "request_type": request_type,
        "requester_email": requester_email,
        "requester_name": requester_name,
        "candidate_id": candidate_id,
        "status": "pending",
    }).execute()

    return result.data[0] if result.data else {}


def process_data_rights_request(
    org_id: str,
    request_id: str,
    new_status: str,
    completed_by: Optional[str] = None,
    notes: str = "",
) -> Dict:
    """Move a data rights request through its lifecycle and act on it.

    Completing an ``erasure`` request runs services.compliance.erasure for
    the requester (e-mail, plus candidate_id when set) and stores the erasure
    report on the request as the completion record (GDPR-E-002). Completing
    an ``access``/``portability`` request attaches the export so the owner
    can send it. A status change alone never counts as fulfilment.
    """
    valid_statuses = {"in_progress", "completed", "rejected"}
    if new_status not in valid_statuses:
        raise ValueError(f"status must be one of: {', '.join(valid_statuses)}")

    from config.clients import supabase_client
    from datetime import datetime, timezone

    existing = supabase_client.table("data_rights_requests") \
        .select("id, request_type, requester_email, candidate_id, status") \
        .eq("id", request_id) \
        .eq("organization_id", org_id) \
        .limit(1) \
        .execute()
    if not existing.data:
        return {}
    req = existing.data[0] or {}

    updates = {"status": new_status, "notes": notes}
    fulfilment: Optional[Dict] = None
    if new_status == "completed":
        updates["completed_at"] = datetime.now(timezone.utc).isoformat()
        updates["completed_by"] = completed_by
        if req.get("request_type") == "erasure":
            from services.compliance.erasure import erase_candidate
            fulfilment = erase_candidate(
                profile_id=req.get("candidate_id"),
                email=req.get("requester_email"),
                reason="erasure_request",
                source=f"data_rights:{request_id}",
            )
        elif req.get("request_type") in ("access", "portability"):
            from services.compliance.erasure import export_candidate
            fulfilment = export_candidate(
                email=req.get("requester_email"),
                profile_id=req.get("candidate_id"),
            )
        if fulfilment is not None:
            # No dedicated column (code-only fix): the completion record is
            # kept in notes as JSON so it survives with the request row.
            import json
            record = "fulfilment: " + json.dumps(fulfilment, default=str)
            updates["notes"] = f"{notes}\n\n{record}" if notes else record

    result = supabase_client.table("data_rights_requests") \
        .update(updates) \
        .eq("id", request_id) \
        .eq("organization_id", org_id) \
        .execute()

    row = dict(result.data[0]) if result.data else {}
    if fulfilment is not None:
        row["fulfilment"] = fulfilment
    return row
