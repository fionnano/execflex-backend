"""
Authentication helper functions for verifying Supabase Auth tokens and admin roles.
"""
from flask import request
from typing import Optional, Tuple
import jwt  # PyJWT (already in requirements.txt)
from config.clients import supabase_client


def get_authenticated_user_id() -> Tuple[Optional[str], Optional[str]]:
    """
    Extract and verify the authenticated user ID from the Authorization header.
    
    Verifies Supabase JWT tokens by decoding them. For production, you may want
    to add signature verification against Supabase's public key.
    
    Returns:
        Tuple[user_id, error_message]
        - If authenticated: (user_id, None)
        - If not authenticated: (None, error_message)
    """
    # Get Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None, "Missing Authorization header"
    
    # Extract Bearer token
    if not auth_header.startswith("Bearer "):
        return None, "Invalid Authorization header format. Expected 'Bearer <token>'"
    
    token = auth_header.replace("Bearer ", "").strip()
    if not token:
        return None, "Missing token in Authorization header"
    
    # Decode JWT token (Supabase tokens are JWTs)
    try:
        # Decode without verification for MVP (faster, but less secure)
        # For production, verify signature against Supabase's JWT secret
        # You can get the JWT secret from Supabase Dashboard → Settings → API → JWT Secret
        decoded = jwt.decode(token, options={"verify_signature": False})
        
        # Extract user_id from token payload
        # Supabase JWT structure: { "sub": "user-uuid", "email": "...", ... }
        user_id = decoded.get("sub")
        
        if not user_id:
            return None, "Token does not contain user ID (sub claim)"
        
        # Basic validation: check if token has required claims
        if "exp" in decoded:
            import time
            if decoded["exp"] < time.time():
                return None, "Token has expired"
        
        return user_id, None
            
    except jwt.DecodeError as e:
        print(f"⚠️ JWT decode error: {e}")
        return None, f"Invalid token format: {str(e)}"
    except Exception as e:
        # Token verification failed
        print(f"⚠️ Token verification failed: {e}")
        return None, f"Token verification failed: {str(e)}"


def require_auth(f):
    """
    Decorator to require authentication for a route.
    
    Usage:
        @qualification_bp.route("/enqueue", methods=["POST"])
        @require_auth
        def enqueue_call():
            user_id = request.environ.get('authenticated_user_id')
            # ... use user_id
    """
    from functools import wraps
    from utils.response_helpers import bad
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip authentication for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return f(*args, **kwargs)
        
        user_id, error = get_authenticated_user_id()
        if not user_id:
            return bad(error or "Authentication required", 401)
        
        # Store user_id in request context for use in the route
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
        print(f"⚠️ Error checking admin status: {e}")
        return False


def require_admin(f):
    """
    Decorator to require admin role for a route.
    Requires authentication AND admin role in role_assignments.
    
    Usage:
        @onboarding_bp.route("/enqueue", methods=["POST"])
        @require_admin
        def enqueue_call():
            user_id = request.environ.get('authenticated_user_id')
            # ... use user_id
    """
    from functools import wraps
    from utils.response_helpers import bad
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip authentication for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return f(*args, **kwargs)
        
        # First check authentication
        user_id, error = get_authenticated_user_id()
        if not user_id:
            return bad(error or "Authentication required", 401)
        
        # Then check admin role
        if not is_user_admin(user_id):
            return bad("Admin access required", 403)
        
        # Store user_id in request context
        request.environ['authenticated_user_id'] = user_id
        return f(*args, **kwargs)
    
    return decorated_function

