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
    """Update a data rights request status."""
    valid_statuses = {"in_progress", "completed", "rejected"}
    if new_status not in valid_statuses:
        raise ValueError(f"status must be one of: {', '.join(valid_statuses)}")

    from config.clients import supabase_client
    updates = {"status": new_status, "notes": notes}
    if new_status == "completed":
        from datetime import datetime, timezone
        updates["completed_at"] = datetime.now(timezone.utc).isoformat()
        updates["completed_by"] = completed_by

    result = supabase_client.table("data_rights_requests") \
        .update(updates) \
        .eq("id", request_id) \
        .eq("organization_id", org_id) \
        .execute()

    return result.data[0] if result.data else {}
