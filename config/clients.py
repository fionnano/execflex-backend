"""
Initialize external service clients (Supabase, Twilio, OpenAI).
"""
from config.app_config import (
    SUPABASE_URL,
    SUPABASE_KEY,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    OPENAI_API_KEY
)

# Supabase (required)
try:
    from supabase import create_client, Client  # type: ignore
except ImportError as e:
    raise ImportError("Supabase client is required. Install: pip install supabase") from e

# Twilio for voice calls (optional)
try:
    from twilio.rest import Client as TwilioClient
    from twilio.twiml.voice_response import VoiceResponse, Gather
    TWILIO_AVAILABLE = True
except ImportError:
    print("⚠️ Twilio not installed. Voice features will be unavailable. Install: pip install twilio")
    TwilioClient = None
    VoiceResponse = None
    Gather = None
    TWILIO_AVAILABLE = False

# OpenAI for natural conversation rephrasing (optional)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("⚠️ OpenAI not installed. GPT rephrasing will be unavailable. Install: pip install openai")


# Initialize Supabase client (required)
try:
    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialised.")
except Exception as e:
    raise RuntimeError(f"Failed to initialize Supabase client: {e}") from e


# Initialize Twilio client (optional - voice features)
twilio_client = None
if TWILIO_AVAILABLE and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        print("✅ Twilio client initialised.")
    except Exception as e:
        print(f"⚠️ Twilio client initialization failed: {e}")


# Initialize OpenAI client (optional - for natural conversation)
gpt_client = None
if OPENAI_AVAILABLE and OPENAI_API_KEY:
    try:
        gpt_client = OpenAI(api_key=OPENAI_API_KEY)
        print("✅ OpenAI client initialised.")
    except Exception as e:
        print(f"⚠️ OpenAI client initialization failed: {e}")

