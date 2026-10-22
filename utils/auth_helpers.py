"""
Authentication helper functions for verifying Supabase Auth tokens,
service-to-service keys, and admin roles.
"""
from flask import request
from typing import Optional, Tuple
from config.clients import supabase_client


def _check_service_key() -> Tuple[Optional[str], bool]:
    """
    Check for a valid X-Service-Key header (service-to-service auth).

    Returns:
        (service_identity, matched)
        - If matched: ("service:ainm", True)
        - If not matched or not present: (None, False)
    """
    import os
    service_key = os.getenv("AINM_SERVICE_KEY")
    if not service_key:
        return None, False

    header_value = request.headers.get("X-Service-Key")
    if not header_value:
        return None, False

    # Constant-time comparison to prevent timing attacks
    import hmac
    try:
        header_bytes = header_value.encode("utf-8")
        key_bytes = service_key.encode("utf-8")
    except Exception:
        return None, False
    if hmac.compare_digest(header_bytes, key_bytes):
        return "service:ainm", True

    return None, False


def get_authenticated_user_id() -> Tuple[Optional[str], Optional[str]]:
    """
    Extract and verify the authenticated user ID from the request.

    Checks (in order):
    1. Smoke-test bypass header (CI/pipeline only)
    2. X-Service-Key header (service-to-service, e.g. Ainm backend)
    3. Authorization: Bearer <JWT> (Supabase user tokens)

    JWT verification:
    - Supabase Auth access tokens are verified by utils.jwt_verify: signature
      (HS256 secret or JWKS), exp, aud, iss, and a fixed algorithm allow-list.
    - If the key for the token's algorithm is not configured, the token is
      rejected. Signature verification is never skipped.

    Returns:
        Tuple[user_id, error_message]
        - If authenticated: (user_id, None)
        - If not authenticated: (None, error_message)
    """
    import os
    # 1. Smoke-test bypass for CI/pipeline — NEVER enable in production
    smoke_secret = os.getenv("SMOKE_TEST_BYPASS_SECRET")
    smoke_user_id = os.getenv("SMOKE_TEST_USER_ID")
    if smoke_secret and smoke_user_id:
        if os.getenv("FLASK_ENV") == "production" or os.getenv("APP_ENV") == "production":
            if not getattr(get_authenticated_user_id, "_warned_smoke_prod", False):
                print("SECURITY WARNING: SMOKE_TEST_BYPASS_SECRET is set in production — ignoring it")
                get_authenticated_user_id._warned_smoke_prod = True
        elif request.headers.get("X-Smoke-Test") == smoke_secret:
            return smoke_user_id, None

    # 2. Service-to-service key (e.g. Ainm backend calling ExecFlex)
    service_id, matched = _check_service_key()
    if matched:
        return service_id, None

    # 3. Bearer JWT token
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None, "Missing Authorization header"

    if not auth_header.startswith("Bearer "):
        return None, "Invalid Authorization header format. Expected 'Bearer <token>'"

    token = auth_header.replace("Bearer ", "").strip()
    if not token:
        return None, "Missing token in Authorization header"

    try:
        from utils.jwt_verify import TokenVerificationError, verify_supabase_access_token

        decoded = verify_supabase_access_token(token)
        return decoded.get("sub"), None
    except TokenVerificationError as exc:
        return None, exc.message


def require_auth(f):
    """
    Decorator to require authentication for a route.
    Accepts JWT tokens, service keys, or smoke-test bypass.

    Usage:
        @bp.route("/endpoint", methods=["POST"])
        @require_auth
        def handler():
            user_id = request.environ.get('authenticated_user_id')
            # ... use user_id
    """
    from functools import wraps
    from utils.response_helpers import bad

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == "OPTIONS":
            from flask import make_response
            response = make_response()
            response.status_code = 200
            return response

        user_id, error = get_authenticated_user_id()
        if not user_id:
            return bad(error or "Authentication required", 401)

        request.environ['authenticated_user_id'] = user_id
        return f(*args, **kwargs)

    return decorated_function


def is_user_admin(user_id: str) -> bool:
    """
    Check if a user has the 'admin' role in role_assignments table.

    Args:
        user_id: User UUID to check

    Returns:
        True if user has 'admin' role, False otherwise
    """
    if not user_id or not supabase_client:
        return False

    try:
        result = supabase_client.table("role_assignments")\
            .select("role")\
            .eq("user_id", user_id)\
            .eq("role", "admin")\
            .limit(1)\
            .execute()

        return len(result.data) > 0 if result.data else False
    except Exception as e:
        print(f"Error checking admin status: {e}")
        return False


def require_admin(f):
    """
    Decorator to require admin role for a route.
    Requires authentication AND admin role in role_assignments.

    Usage:
        @bp.route("/admin-endpoint", methods=["POST"])
        @require_admin
        def handler():
            user_id = request.environ.get('authenticated_user_id')
            # ... use user_id
    """
    from functools import wraps
    from utils.response_helpers import bad

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == "OPTIONS":
            from flask import make_response
            response = make_response()
            response.status_code = 200
            return response

        user_id, error = get_authenticated_user_id()
        if not user_id:
            return bad(error or "Authentication required", 401)

        if not is_user_admin(user_id):
            return bad("Admin access required", 403)

        request.environ['authenticated_user_id'] = user_id
        return f(*args, **kwargs)

    return decorated_function
