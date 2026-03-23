"""
Billing service: tier quota checks and usage counting.
"""
from config.clients import supabase_client


# Tier quotas: {tier: {resource: max_per_month or None for unlimited}}
TIER_QUOTAS = {
    "free": {
        "roles_posted": 1,
        "intros_made": 3,
        "screenings_done": 1,
    },
    "growth": {
        "roles_posted": None,
        "intros_made": None,
        "screenings_done": None,
    },
    "enterprise": {
        "roles_posted": None,
        "intros_made": None,
        "screenings_done": None,
    },
}


def get_organization_for_user(user_id: str) -> dict | None:
    """
    Look up the organization a user belongs to.
    Returns the organization row or None.
    """
    if not supabase_client:
        return None

    # Check if user created any organization
    resp = (
        supabase_client.table("organizations")
        .select("*")
        .eq("created_by_user_id", user_id)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]

    # Check if user posted an opportunity that links to an org
    opp_resp = (
        supabase_client.table("opportunities")
        .select("organization_id")
        .eq("created_by_user_id", user_id)
        .limit(1)
        .execute()
    )
    if opp_resp.data and opp_resp.data[0].get("organization_id"):
        org_resp = (
            supabase_client.table("organizations")
            .select("*")
            .eq("id", opp_resp.data[0]["organization_id"])
            .limit(1)
            .execute()
        )
        if org_resp.data:
            return org_resp.data[0]

    return None


def get_usage_this_month(user_id: str) -> dict:
    """
    Count how many roles, intros, and screenings the user has used this month.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    roles_posted = 0
    intros_made = 0
    screenings_done = 0

    if not supabase_client:
        return {"roles_posted": 0, "intros_made": 0, "screenings_done": 0}

    try:
        # Count opportunities created this month by this user
        r = (
            supabase_client.table("opportunities")
            .select("id", count="exact")
            .eq("created_by_user_id", user_id)
            .gte("created_at", month_start)
            .execute()
        )
        roles_posted = r.count if r.count is not None else len(r.data or [])
    except Exception:
        pass

    try:
        # Count intro emails (interactions with channel=email, direction=outbound)
        r = (
            supabase_client.table("interactions")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .eq("channel", "email")
            .eq("direction", "outbound")
            .gte("created_at", month_start)
            .execute()
        )
        intros_made = r.count if r.count is not None else len(r.data or [])
    except Exception:
        pass

    try:
        # Count screening call jobs created this month
        r = (
            supabase_client.table("outbound_call_jobs")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .gte("created_at", month_start)
            .execute()
        )
        screenings_done = r.count if r.count is not None else len(r.data or [])
    except Exception:
        pass

    return {
        "roles_posted": roles_posted,
        "intros_made": intros_made,
        "screenings_done": screenings_done,
    }


def check_quota(user_id: str, resource: str) -> tuple[bool, str | None]:
    """
    Check if a user has remaining quota for the given resource.

    Args:
        user_id: Authenticated user ID
        resource: One of 'roles_posted', 'intros_made', 'screenings_done'

    Returns:
        (allowed, error_message)
        - (True, None) if within quota
        - (False, message) if quota exceeded
    """
    # Service accounts bypass quota
    if user_id.startswith("service:"):
        return True, None

    org = get_organization_for_user(user_id)
    tier = (org or {}).get("subscription_tier", "free")
    status = (org or {}).get("subscription_status", "free")

    # Active or trialing subscriptions get their tier quota
    if status not in ("active", "trialing", "free"):
        tier = "free"

    quotas = TIER_QUOTAS.get(tier, TIER_QUOTAS["free"])
    limit = quotas.get(resource)

    # None means unlimited
    if limit is None:
        return True, None

    usage = get_usage_this_month(user_id)
    current = usage.get(resource, 0)

    if current >= limit:
        resource_label = resource.replace("_", " ")
        return False, (
            f"Free tier limit reached: {limit} {resource_label} per month. "
            f"Upgrade to Growth for unlimited access."
        )

    return True, None
