"""
Authentication helper functions for verifying Supabase Auth tokens,
service-to-service keys, and admin roles.
"""
from flask import request
from typing import Optional, Tuple
import jwt  # PyJWT (already in requirements.txt)
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
    if hmac.compare_digest(header_value, service_key):
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
    - If SUPABASE_JWT_SECRET is set, tokens are verified against it.
    - If not set (dev mode), tokens are decoded without signature verification
      and a warning is logged.

    Returns:
        Tuple[user_id, error_message]
        - If authenticated: (user_id, None)
        - If not authenticated: (None, error_message)
    """
    import os
    # 1. Smoke-test bypass for CI/pipeline
    smoke_secret = os.getenv("SMOKE_TEST_BYPASS_SECRET")
    smoke_user_id = os.getenv("SMOKE_TEST_USER_ID")
    if smoke_secret and smoke_user_id:
        if request.headers.get("X-Smoke-Test") == smoke_secret:
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
        jwt_secret = os.getenv("SUPABASE_JWT_SECRET")

        if jwt_secret:
            # Production: verify signature against Supabase JWT secret
            decoded = jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                options={"verify_exp": True, "verify_aud": False},
            )
        else:
            # Dev mode: decode without verification (log warning once)
            if not getattr(get_authenticated_user_id, "_warned_no_secret", False):
                print("WARNING: SUPABASE_JWT_SECRET not set — JWT signature verification disabled (dev mode)")
                get_authenticated_user_id._warned_no_secret = True
            decoded = jwt.decode(token, options={"verify_signature": False})

            # Manual expiration check for unverified tokens
            if "exp" in decoded:
                import time
                if decoded["exp"] < time.time():
                    return None, "Token has expired"

        user_id = decoded.get("sub")
        if not user_id:
            return None, "Token does not contain user ID (sub claim)"

        return user_id, None

    except jwt.ExpiredSignatureError:
        return None, "Token has expired"
    except jwt.InvalidSignatureError:
        return None, "Invalid token signature"
    except jwt.DecodeError as e:
        print(f"JWT decode error: {e}")
        return None, f"Invalid token format: {str(e)}"
    except Exception as e:
        print(f"Token verification failed: {e}")
        return None, f"Token verification failed: {str(e)}"


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
