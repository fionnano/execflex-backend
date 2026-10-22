"""
Org-scoped authentication middleware.

Every API endpoint gets an OrgContext injected — org_id comes from JWT claims,
never from the request body. This eliminates S-003 (filter injection) by design.
"""
from dataclasses import dataclass
from functools import wraps
from typing import Optional

from flask import g, request, jsonify


@dataclass
class OrgContext:
    user_id: str
    org_id: str
    role: str  # owner | recruiter | viewer


def extract_org_context() -> Optional[OrgContext]:
    """Extract org context from request. Returns None if not authenticated."""
    if hasattr(g, 'org_context'):
        return g.org_context

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:].strip()
    if not token:
        return None
    try:
        from utils.jwt_verify import (
            TokenVerificationError,
            org_claims_from_payload,
            verify_supabase_access_token,
        )
        payload = verify_supabase_access_token(token)
        org_id, role = org_claims_from_payload(payload)
        ctx = OrgContext(user_id=payload["sub"], org_id=org_id, role=role)
        g.org_context = ctx
        return ctx
    except TokenVerificationError:
        return None
    except Exception as exc:
        print(f"Org context verification failed: {type(exc).__name__}", flush=True)
        return None


def require_org(allowed_roles=None):
    """Decorator requiring authenticated org context.

    Usage:
        @require_org()  # any authenticated user
        @require_org(allowed_roles=["owner", "recruiter"])  # specific roles
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ctx = extract_org_context()
            if not ctx:
                return jsonify({"error": "Authentication required"}), 401
            if not ctx.org_id:
                return jsonify({"error": "Organization context required"}), 403
            if allowed_roles and ctx.role not in allowed_roles:
                return jsonify({"error": "Insufficient permissions"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


def get_org_context() -> OrgContext:
    """Get the current org context. Call only inside @require_org."""
    return g.org_context
