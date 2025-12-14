"""
Text-to-Speech service using ElevenLabs with caching.
"""
import uuid
import requests
from pathlib import Path
from config.app_config import ELEVEN_API_KEY, ELEVEN_VOICE_ID

# TTS cache directory
CACHE_DIR = Path("static/audio")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TTS_CACHE = {}  # text -> path

# Common prompts to pre-cache
COMMON_PROMPTS = [
    "Hi, I'm Ai-dan, your advisor at ExecFlex. Let's keep this simple. Are you hiring for a role, or are you a candidate looking for opportunities?",
    "Hello, this is ExecFlex. We're calling to welcome you and learn more about your needs. Are you looking to hire executive talent, or are you an executive looking for opportunities?",
    "Hello, this is ExecFlex. We're calling to welcome you and help you find executive opportunities. Let's get started with a few quick questions.",
    "Hello, this is ExecFlex. We're calling to welcome you and help you find executive talent. Let's get started with a few quick questions.",
    "Great, thanks. What's your first name?",
    "Nice to meet you. Which leadership role are you focused on — for example CFO, CEO, or CTO?",
    "Got it. And which industry are you most focused on — like fintech, insurance, or SaaS?",
    "Perfect. Do you have a location preference — Ireland, the UK, or would remote work?",
    "Okay. And do you see this role as fractional — a few days a week — or full time?",
    "Based on what you've told me, I have someone in mind. Would you like me to make a warm email introduction?",
    "Perfect. What's the best email address for me to send the introduction to?",
    "Great. I've emailed the introduction. Goodbye.",
    "Sorry, I didn't catch that. Let's try again quickly."
]


def generate_tts(text: str) -> str:
    """
    Generate TTS from ElevenLabs (or return cached).
    
    Args:
        text: Text to convert to speech
        
    Returns:
        Relative path to audio file, or empty string if generation failed
    """
    if text in TTS_CACHE:
        return TTS_CACHE[text]

    if not ELEVEN_API_KEY or not ELEVEN_VOICE_ID:
        print("⚠️ ElevenLabs not configured. TTS unavailable.")
        return ""

    filename = f"{uuid.uuid4()}.mp3"
    filepath = CACHE_DIR / filename

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    data = {
        "text": text,
        "voice_settings": {
            "stability": 0.25,
            "similarity_boost": 0.95,
            "style": 0.8,
            "use_speaker_boost": True
        }
    }

    print("DEBUG Generating TTS:", text[:80])
    try:
        r = requests.post(url, headers=headers, json=data, timeout=60)
        r.raise_for_status()

        with open(filepath, "wb") as f:
            f.write(r.content)

        rel_path = f"/static/audio/{filename}"
        TTS_CACHE[text] = rel_path
        return rel_path
    except Exception as e:
        print(f"⚠️ TTS generation failed: {e}")
        return ""


def pre_cache_common_prompts():
    """Pre-cache common prompts at startup."""
    print("DEBUG Pre-caching common prompts...")
    for prompt in COMMON_PROMPTS:
        try:
            generate_tts(prompt)
        except Exception as e:
            print("⚠️ Could not cache:", prompt[:50], e)


def get_cache_size() -> int:
    """Get the number of cached TTS items."""
    return len(TTS_CACHE)


def get_cached_audio_path(text: str) -> str:
    """
    Get the cached audio file path for a given text prompt.
    
    Args:
        text: The text prompt to look up
        
    Returns:
        Relative path to audio file (e.g., "/static/audio/{filename}.mp3") or empty string if not found
    """
    return TTS_CACHE.get(text, "")

