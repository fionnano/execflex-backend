"""
Twilio webhook signature verification utilities.
"""
from flask import request
from config.app_config import TWILIO_AUTH_TOKEN
import os
from typing import Optional

# Try to import Twilio's RequestValidator (preferred method)
try:
    from twilio.request_validator import RequestValidator
    TWILIO_VALIDATOR_AVAILABLE = True
except ImportError:
    TWILIO_VALIDATOR_AVAILABLE = False
    print("⚠️ Twilio RequestValidator not available. Install: pip install twilio")


def _twilio_auth_token() -> Optional[str]:
    """Runtime token so tests and deploys can set it after import."""
    return (os.getenv("TWILIO_AUTH_TOKEN") or TWILIO_AUTH_TOKEN or "").strip() or None


def verify_twilio_signature(url: Optional[str] = None) -> bool:
    """
    Verify that a Twilio webhook request is authentic.

    Uses Twilio's RequestValidator if available, otherwise falls back to manual verification.

    A missing auth token or a failed signature returns False. Unsigned webhooks
    are accepted only when TWILIO_ALLOW_UNSIGNED_WEBHOOKS is set outside production.

    Args:
        url: Optional full URL of the webhook endpoint (defaults to request.url)

    Returns:
        True if signature is valid, False otherwise
    """
    from utils.security_env import unsigned_twilio_webhooks_allowed

    token = _twilio_auth_token()
    if unsigned_twilio_webhooks_allowed():
        print("⚠️ TWILIO_ALLOW_UNSIGNED_WEBHOOKS is set — skipping signature verification (not production).")
        return True
    if not token:
        print("⚠️ TWILIO_AUTH_TOKEN is not configured — refusing unsigned Twilio webhook.")
        return False
    
    # Get signature from header
    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        print("⚠️ Missing X-Twilio-Signature header")
        return False
    
    # Use Twilio's RequestValidator if available (recommended)
    if TWILIO_VALIDATOR_AVAILABLE:
        try:
            validator = RequestValidator(token)
            
            # RequestValidator can use request.url directly (handles proxy headers automatically)
            # If explicit URL provided, use it; otherwise let RequestValidator use request.url
            url_to_validate = url if url else request.url
            
            # RequestValidator can accept request.form (MultiDict) directly
            # According to Twilio docs, it handles MultiDict internally
            is_valid = validator.validate(url_to_validate, request.form, signature)
            
            if not is_valid:
                print(f"⚠️ Signature verification failed (using Twilio RequestValidator)")
                print(f"   URL used: {url_to_validate}")
                print(f"   POST params count: {len(request.form)}")
                print(f"   Request URL: {request.url}")
                print(f"   X-Forwarded-Proto: {request.headers.get('X-Forwarded-Proto')}")
                print(f"   X-Forwarded-Host: {request.headers.get('X-Forwarded-Host')}")
                print("   Auth Token configured: Yes")
                print(f"   Auth Token length: {len(token)}")
                print(f"   Signature header: {signature[:20]}..." if signature and len(signature) > 20 else f"   Signature header: {signature}")
                # Log first few POST params for debugging (don't log sensitive data)
                if request.form:
                    param_keys = list(request.form.keys())[:5]
                    print(f"   Sample POST param keys: {param_keys}")
            
            return is_valid
        except Exception as e:
            print(f"⚠️ Error using Twilio RequestValidator: {e}")
            import traceback
            traceback.print_exc()
            # Fall through to manual verification
    
    # Fallback to manual verification (if RequestValidator not available)
    import hmac
    import hashlib
    from urllib.parse import urlencode
    import base64
    
    # Use provided URL or request.url
    url_to_validate = url if url else request.url
    
    # Get all POST parameters
    params = {}
    for key in request.form:
        params[key] = request.form[key]
    
    # Sort parameters by key
    sorted_params = sorted(params.items())
    
    # Build data string: URL + sorted parameters (URL should NOT include query string)
    # Twilio's signature is computed on: URL (without query) + sorted POST params
    url_without_query = url_to_validate.split('?')[0]
    data = url_without_query + urlencode(sorted_params)
    
    # Compute HMAC
    computed_signature = hmac.new(
        token.encode('utf-8'),
        data.encode('utf-8'),
        hashlib.sha1
    ).digest()
    
    # Base64 encode
    computed_signature_b64 = base64.b64encode(computed_signature).decode('utf-8')
    
    # Compare (use constant-time comparison to prevent timing attacks)
    is_valid = hmac.compare_digest(computed_signature_b64, signature)
    
    if not is_valid:
        print(f"⚠️ Signature verification failed (manual verification)")
        print(f"   URL used: {url_without_query}")
        print(f"   Params: {sorted_params[:3]}...")  # Only show first 3 params
    
    return is_valid


def twilio_request_is_authentic(canonical_path: str) -> bool:
    """
    True when the current request carries a valid Twilio signature.

    Render's proxy can make request.url differ from the URL Twilio signed.
    On failure, retry against the public base URL plus canonical_path
    (for example ``/voice/status``).
    """
    if verify_twilio_signature():
        return True
    base_url = (
        os.getenv("API_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or "https://execflex-backend-1.onrender.com"
    ).rstrip("/")
    canonical = f"{base_url}{canonical_path}"
    if request.query_string:
        canonical += f"?{request.query_string.decode()}"
    return verify_twilio_signature(url=canonical)


def require_twilio_signature(f):
    """
    Decorator to require valid Twilio signature for webhook endpoints.
    
    Usage:
        @onboarding_bp.route("/onboarding/turn", methods=["POST"])
        @require_twilio_signature
        def handle_turn():
            # ... handle webhook
    """
    from functools import wraps
    from flask import Response
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not verify_twilio_signature():
            print("❌ Invalid Twilio signature")
            return Response("Invalid signature", status=403), 403
        return f(*args, **kwargs)
    
    return decorated_function

