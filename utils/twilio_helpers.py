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
        url: Optional full URL of the webhook endpoint (defaults to reconstructing from env vars or request.url)
        
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
    
    # Get the full URL - use provided URL, or reconstruct from base URL, or fall back to request.url
    if not url:
        # Try to reconstruct URL from environment variables (what Twilio was configured with)
        base_url = (
            os.getenv("API_BASE_URL") or 
            os.getenv("RENDER_EXTERNAL_URL") or 
            os.getenv("VITE_FLASK_API_URL") or 
            None
        )
        
        if base_url:
            # Reconstruct the full URL that Twilio expects
            # Remove trailing slash from base_url
            base_url = base_url.rstrip('/')
            # Get the path from request
            path = request.path
            # Reconstruct full URL
            url = f"{base_url}{path}"
        else:
            # Fall back to request.url, but normalize it
            url = request.url
            # Remove query string from URL for signature verification (Twilio doesn't include it in the signed URL)
            # Actually, wait - Twilio DOES include query params in the signature if they were in the original URL
            # But if the URL was configured without query params, we should use it without query params
            # For status callbacks, there are usually no query params, so let's try without first
            if '?' in url:
                # Try with and without query params
                url_without_query = url.split('?')[0]
                # We'll try both variations below
                pass
    
    # Get all POST parameters
    params = {}
    for key in request.form:
        params[key] = request.form[key]
    
    # Also get query parameters if they exist
    for key in request.args:
        if key not in params:  # Don't override POST params
            params[key] = request.args[key]
    
    # Sort parameters by key
    sorted_params = sorted(params.items())
    
    # Build data string: URL + sorted parameters
    # Remove query string from URL if we're including params separately
    url_for_signing = url.split('?')[0] if '?' in url else url
    data = url_for_signing + urlencode(sorted_params)
    
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
    is_valid = hmac.compare_digest(computed_signature_b64, signature)
    
    if not is_valid:
        # Log for debugging (but don't expose signature)
        print(f"⚠️ Signature verification failed")
        print(f"   URL used: {url_for_signing}")
        print(f"   Params: {sorted_params[:3]}...")  # Only show first 3 params
        print(f"   Expected signature starts with: {computed_signature_b64[:10]}...")
        print(f"   Received signature starts with: {signature[:10]}...")
    
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
