"""
Organisation scoping for legacy (non-/api/v1) routes.

Legacy routes authenticate with @require_auth, which yields a Supabase user id
or the service identity "service:ainm" but no organisation. The Supabase
service-role key bypasses RLS, so every handler has to scope its own reads.
These helpers resolve the organisation of the caller and of a screening job so
handlers can compare the two (SEC-E-003, SEC-E-004).
"""
import uuid
from typing import Any, Dict, Optional


def is_uuid(value: Any) -> bool:
    """True if value parses as a UUID (any version)."""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def is_service_identity(user_id: Optional[str]) -> bool:
    """True for the X-Service-Key identity ("service:ainm")."""
    return bool(user_id) and str(user_id).startswith("service:")


def resolve_caller_org_id(user_id: Optional[str]) -> Optional[str]:
    """
    Organisation of the authenticated caller, or None.

    Uses the verified JWT's app_metadata.org_id when present (the same claim
    /api/v1 trusts), else the caller's user_roles membership. Service callers
    have no organisation.
    """
    if not user_id or is_service_identity(user_id):
        return None
    try:
        from services.api.auth import extract_org_context
        ctx = extract_org_context()
        if ctx and ctx.user_id == user_id and ctx.org_id:
            return ctx.org_id
    except Exception:
        pass
    return _org_from_user_roles(user_id)


def resolve_job_org_id(job: Dict[str, Any]) -> Optional[str]:
    """
    Organisation that owns an outbound_call_jobs row, or None.

    Order: artifacts.organization_id (stamped when the job is created), the
    organisation of the opportunity in screening_context.role_id, then the
    creator's user_roles membership.
    """
    artifacts = job.get("artifacts") or {}
    org_id = artifacts.get("organization_id")
    if org_id:
        return org_id
    role_id = (artifacts.get("screening_context") or {}).get("role_id")
    org_id = _org_from_opportunity(role_id)
    if org_id:
        return org_id
    return _org_from_user_roles(job.get("user_id"))


def load_opportunity_for_owner(user_id: Optional[str], opportunity_id: Optional[str],
                               columns: str = "id, organization_id, created_by_user_id") -> Optional[Dict[str, Any]]:
    """
    The opportunities row if the caller may act on it, else None.

    A caller owns an opportunity when it belongs to their organisation or
    they created it. Service identities own nothing here; malformed ids
    never reach the database (SEC-E-007, SEC-E-010).
    """
    from config.clients import supabase_client
    if not supabase_client or not user_id or is_service_identity(user_id) or not is_uuid(opportunity_id):
        return None
    wanted = set(c.strip() for c in columns.split(","))
    wanted.update({"id", "organization_id", "created_by_user_id"})
    try:
        resp = (
            supabase_client.table("opportunities")
            .select(", ".join(sorted(wanted)))
            .eq("id", str(opportunity_id))
            .limit(1)
            .execute()
        )
    except Exception:
        return None
    rows = resp.data or []
    if not rows:
        return None
    opp = rows[0] or {}
    if opp.get("created_by_user_id") == user_id:
        return opp
    caller_org = resolve_caller_org_id(user_id)
    if caller_org and opp.get("organization_id") == caller_org:
        return opp
    return None


def caller_owns_opportunity(user_id: Optional[str], opportunity_id: Optional[str]) -> bool:
    return load_opportunity_for_owner(user_id, opportunity_id) is not None


def _org_from_user_roles(user_id: Optional[str]) -> Optional[str]:
    from config.clients import supabase_client
    if not supabase_client or not is_uuid(user_id):
        return None
    try:
        resp = (
            supabase_client.table("user_roles")
            .select("organization_id")
            .eq("user_id", str(user_id))
            .order("created_at")
            .limit(1)
            .execute()
        )
    except Exception:
        return None
    rows = resp.data or []
    return (rows[0] or {}).get("organization_id") if rows else None


def _org_from_opportunity(opportunity_id: Optional[str]) -> Optional[str]:
    from config.clients import supabase_client
    if not supabase_client or not is_uuid(opportunity_id):
        return None
    try:
        resp = (
            supabase_client.table("opportunities")
            .select("organization_id")
            .eq("id", str(opportunity_id))
            .limit(1)
            .execute()
        )
    except Exception:
        return None
    rows = resp.data or []
    return (rows[0] or {}).get("organization_id") if rows else None
