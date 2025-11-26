"""
Application configuration and environment variable management.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# App configuration
APP_ENV = os.getenv("APP_ENV", "dev")
PORT = int(os.getenv("PORT", 5001))

# Supabase configuration (required)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # Service role key recommended for server-side
EMAIL_ADDRESS = os.getenv("EMAIL_USER")

# Voice/Telephony configuration (optional)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER") or os.getenv("TWILIO_PHONE")

# ElevenLabs TTS configuration (optional)
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY") or os.getenv("ELEVENLABS_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVEN_VOICE_ID") or os.getenv("ELEVENLABS_VOICE_ID")

# OpenAI configuration (optional)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY".lower()) or os.getenv("openai_api_key")


def validate_config():
    """Validate required configuration."""
    if not SUPABASE_URL:
        raise ValueError("SUPABASE_URL environment variable is required")
    if not SUPABASE_KEY:
        raise ValueError("SUPABASE_SERVICE_KEY environment variable is required")
    return True


def print_config_status():
    """Print configuration status at startup."""
    print("✅ Configuration loaded:")
    print(f"  APP_ENV={APP_ENV}")
    print(f"  Email User={EMAIL_ADDRESS}")
    print(f"  Supabase URL present? {bool(SUPABASE_URL)}")
    print(f"  Twilio configured? {bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN)}")
    print(f"  ElevenLabs configured? {bool(ELEVEN_API_KEY and ELEVEN_VOICE_ID)}")
    print(f"  OpenAI configured? {bool(OPENAI_API_KEY)}")
    print("--------------------------------------------------")

