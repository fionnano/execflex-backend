"""
Health check routes.
"""
from flask import request
from routes import health_bp
from utils.response_helpers import ok
from config.app_config import APP_ENV
from config.clients import supabase_client, twilio_client, gpt_client
from config.app_config import ELEVEN_API_KEY, ELEVEN_VOICE_ID
from services.tts_service import get_cache_size


@health_bp.route("/", methods=["GET"])
def root_health():
    """Simple root health check."""
    return "✅ Backend is live!", 200


@health_bp.route("/health", methods=["GET"])
def health():
    """Combined health check for API and voice features."""
    return ok({
        "env": APP_ENV,
        "supabase_connected": bool(supabase_client),
        "twilio_configured": bool(twilio_client),
        "elevenlabs_configured": bool(ELEVEN_API_KEY and ELEVEN_VOICE_ID),
        "openai_configured": bool(gpt_client),
        "tts_cache_items": get_cache_size()
    })

