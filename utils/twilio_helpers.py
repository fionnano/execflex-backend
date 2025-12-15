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


def verify_twilio_signature(url: Optional[str] = None) -> bool:
    """
    Verify that a Twilio webhook request is authentic.
    
    Uses Twilio's RequestValidator if available, otherwise falls back to manual verification.
    
    Args:
        url: Optional full URL of the webhook endpoint (defaults to request.url)
        
    Returns:
        True if signature is valid, False otherwise
    """
    # Allow in development if token not set (for local testing with ngrok)
    app_env = os.getenv("APP_ENV", "prod").lower()
    if not TWILIO_AUTH_TOKEN or app_env == "dev":
        print(f"⚠️ TWILIO_AUTH_TOKEN not configured or in dev mode (APP_ENV={app_env}). Skipping signature verification.")
        return True  # Allow in development if token not set
    
    # Get signature from header
    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        print("⚠️ Missing X-Twilio-Signature header")
        return False
    
    # Use Twilio's RequestValidator if available (recommended)
    if TWILIO_VALIDATOR_AVAILABLE:
        try:
            validator = RequestValidator(TWILIO_AUTH_TOKEN)
            
            # Use provided URL or reconstruct from request
            if url:
                url_to_validate = url
            else:
                # Reconstruct URL from request, handling proxy headers
                # Render uses X-Forwarded-Proto and X-Forwarded-Host
                proto = request.headers.get('X-Forwarded-Proto', 'https')
                host = request.headers.get('X-Forwarded-Host') or request.host
                url_to_validate = f"{proto}://{host}{request.path}"
                if request.query_string:
                    url_to_validate += f"?{request.query_string.decode()}"
            
            # RequestValidator can accept request.form (MultiDict) directly
            # According to Twilio docs, it handles MultiDict internally
            # Validate using Twilio's validator
            is_valid = validator.validate(url_to_validate, request.form, signature)
            
            if not is_valid:
                print(f"⚠️ Signature verification failed (using Twilio RequestValidator)")
                print(f"   URL used: {url_to_validate}")
                print(f"   POST params count: {len(request.form)}")
                print(f"   Request URL: {request.url}")
                print(f"   X-Forwarded-Proto: {request.headers.get('X-Forwarded-Proto')}")
                print(f"   X-Forwarded-Host: {request.headers.get('X-Forwarded-Host')}")
                print(f"   Auth Token configured: {'Yes' if TWILIO_AUTH_TOKEN else 'No'}")
                print(f"   Auth Token length: {len(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else 0}")
            
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
        TWILIO_AUTH_TOKEN.encode('utf-8'),
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

