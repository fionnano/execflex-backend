"""
Twilio webhook signature verification utilities.
"""
from flask import request
from config.app_config import TWILIO_AUTH_TOKEN
import hmac
import hashlib
from urllib.parse import urlencode
from typing import Optional


def verify_twilio_signature(url: Optional[str] = None) -> bool:
    """
    Verify that a Twilio webhook request is authentic.
    
    Args:
        url: Optional full URL of the webhook endpoint (defaults to request.url)
        
    Returns:
        True if signature is valid, False otherwise
    """
    # Allow in development if token not set (for local testing with ngrok)
    import os
    app_env = os.getenv("APP_ENV", "prod").lower()
    if not TWILIO_AUTH_TOKEN or app_env == "dev":
        print(f"⚠️ TWILIO_AUTH_TOKEN not configured or in dev mode (APP_ENV={app_env}). Skipping signature verification.")
        return True  # Allow in development if token not set
    
    # Get signature from header
    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        print("⚠️ Missing X-Twilio-Signature header")
        return False
    
    # Get the full URL
    if not url:
        url = request.url
    
    # Get all POST parameters
    params = {}
    for key in request.form:
        params[key] = request.form[key]
    
    # Sort parameters by key
    sorted_params = sorted(params.items())
    
    # Build data string: URL + sorted parameters
    data = url + urlencode(sorted_params)
    
    # Compute HMAC
    computed_signature = hmac.new(
        TWILIO_AUTH_TOKEN.encode('utf-8'),
        data.encode('utf-8'),
        hashlib.sha1
    ).digest()
    
    # Base64 encode
    import base64
    computed_signature_b64 = base64.b64encode(computed_signature).decode('utf-8')
    
    # Compare (use constant-time comparison to prevent timing attacks)
    return hmac.compare_digest(computed_signature_b64, signature)


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
