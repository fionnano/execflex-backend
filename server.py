# server.py
#
# ExecFlex Combined API Server
# Handles both web API endpoints and voice/telephony features (Ai-dan)
#
# API Routes:
#   - GET  /, /health          - Health checks
#   - POST /match              - Find candidate matches
#   - GET  /matches/<id>       - Get specific match
#   - POST /post-role          - Submit role posting
#   - GET  /view-roles         - List role postings
#   - POST /request-intro      - Request introduction (recommended)
#   - POST /send_intro         - Send intro email (legacy)
#   - POST /feedback           - Submit feedback
#
# Voice Routes:
#   - POST /call_candidate     - Initiate outbound Twilio call
#   - POST /voice/intro        - Twilio webhook (call start)
#   - POST /voice/capture      - Twilio webhook (speech capture)
#
# Environment Variables Required:
#   Required:
#     - SUPABASE_URL           - Supabase project URL
#     - SUPABASE_SERVICE_KEY   - Supabase service role key
#     - EMAIL_USER             - Gmail address for sending emails
#     - EMAIL_PASS             - Gmail password/app password
#     - PORT                   - Server port (default: 5001)
#
#   Optional (for voice features):
#     - TWILIO_ACCOUNT_SID     - Twilio account SID
#     - TWILIO_AUTH_TOKEN      - Twilio auth token
#     - TWILIO_PHONE_NUMBER    - Twilio phone number
#     - ELEVEN_API_KEY         - ElevenLabs API key for TTS
#     - ELEVEN_VOICE_ID        - ElevenLabs voice ID
#     - OPENAI_API_KEY         - OpenAI API key for conversation rephrasing
#
#   Optional (email configuration):
#     - EMAIL_SMTP_HOST        - SMTP host (default: smtp.gmail.com)
#     - EMAIL_SMTP_PORT        - SMTP port (default: 465)

import os
import uuid
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response, url_for
from flask_cors import CORS, cross_origin

# Your existing modules
from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email

# Supabase (required)
try:
    from supabase import create_client, Client  # type: ignore
except ImportError as e:
    raise ImportError("Supabase client is required. Install: pip install supabase") from e

# Twilio for voice calls
try:
    from twilio.rest import Client as TwilioClient
    from twilio.twiml.voice_response import VoiceResponse, Gather
except ImportError as e:
    print("⚠️ Twilio not installed. Voice features will be unavailable. Install: pip install twilio")
    TwilioClient = None
    VoiceResponse = None
    Gather = None

# OpenAI for natural conversation rephrasing
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("⚠️ OpenAI not installed. GPT rephrasing will be unavailable. Install: pip install openai")

# -------------------- ENV & INIT --------------------
load_dotenv()

APP_ENV = os.getenv("APP_ENV", "dev")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # Service role key recommended for server-side
EMAIL_ADDRESS = os.getenv("EMAIL_USER")

# Voice/Telephony configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER") or os.getenv("TWILIO_PHONE")

# ElevenLabs TTS configuration
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY") or os.getenv("ELEVENLABS_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVEN_VOICE_ID") or os.getenv("ELEVENLABS_VOICE_ID")

# OpenAI configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY".lower()) or os.getenv("openai_api_key")

print("✅ server.py booting (combined API + Voice)...")
print(f"APP_ENV={APP_ENV}")
print(f"Email User={EMAIL_ADDRESS}")
print(f"Supabase URL present? {bool(SUPABASE_URL)}")
print(f"Twilio configured? {bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN)}")
print(f"ElevenLabs configured? {bool(ELEVEN_API_KEY and ELEVEN_VOICE_ID)}")
print(f"OpenAI configured? {bool(OPENAI_API_KEY)}")
print("--------------------------------------------------")

# Validate Supabase configuration
if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL environment variable is required")
if not SUPABASE_KEY:
    raise ValueError("SUPABASE_SERVICE_KEY environment variable is required")

app = Flask(__name__, static_folder="static")
# MVP: allow all; lock down to your Lovable domain later
CORS(app, resources={r"/*": {"origins": "*"}})

# Create Supabase client (required)
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialised.")
except Exception as e:
    raise RuntimeError(f"Failed to initialize Supabase client: {e}") from e

# Initialize Twilio client (optional - voice features)
twilio_client = None
if TwilioClient and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
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

# -------------------- TTS (ElevenLabs) & Caching --------------------
CACHE_DIR = Path("static/audio")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TTS_CACHE = {}  # text -> path

def tts_generate(text: str) -> str:
    """Generate TTS from ElevenLabs (or return cached)."""
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

# Pre-cache common prompts
COMMON_PROMPTS = [
    "Hi, I'm Ai-dan, your advisor at ExecFlex. Let's keep this simple. Are you hiring for a role, or are you a candidate looking for opportunities?",
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

print("DEBUG Pre-caching common prompts...")
for p in COMMON_PROMPTS:
    try:
        tts_generate(p)
    except Exception as e:
        print("⚠️ Could not cache:", p[:50], e)

# -------------------- GPT Rephrasing (Optional) --------------------
def gpt_rephrase(context: str, fallback: str) -> str:
    """
    Use GPT to make Ai-dan's reply more natural and human.
    - Keeps it short.
    - Does NOT invent new steps.
    Falls back to the scripted prompt if anything fails.
    """
    if not gpt_client or not OPENAI_API_KEY:
        return fallback
    try:
        prompt = (
            "You are Ai-dan, a friendly executive search consultant at ExecFlex. "
            "Rephrase the following system prompt in a short, natural, conversational way. "
            "Do NOT add new fields or steps. Keep it human and concise.\n\n"
            f"Context so far:\n{context}\n\nSystem Prompt:\n{fallback}"
        )
        resp = gpt_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are Ai-dan."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=80
        )
        out = resp.choices[0].message.content.strip()
        if not out:
            return fallback
        return out
    except Exception as e:
        print("⚠️ GPT rephrase failed:", e)
        return fallback

# -------------------- Session Management (Voice Calls) --------------------
SESSIONS = {}

def _init_session(call_sid: str):
    if call_sid not in SESSIONS:
        SESSIONS[call_sid] = {
            "user_type": None,
            "name": None,
            "email": None,
            "role": None,
            "industry": None,
            "location": None,
            "availability": None,
            "__match": None,
            "_retries": {}
        }
    return SESSIONS[call_sid]

# -------------------- Voice Helper Functions --------------------
def _yn(s: str) -> bool:
    return bool(s) and any(w in s.lower() for w in ["yes", "yeah", "yep", "sure", "please", "ok", "okay"])

def _norm_role(text: str | None) -> str | None:
    if not text: return None
    t = text.lower()
    if "cfo" in t: return "CFO"
    if "ceo" in t: return "CEO"
    if "cto" in t: return "CTO"
    if "coo" in t: return "COO"
    return text.strip().title()

def _norm_industry(text: str | None) -> str | None:
    if not text: return None
    t = text.lower()
    if "fintech" in t or "finance" in t: return "Fintech"
    if "insurance" in t: return "Insurance"
    if "health" in t: return "Healthtech"
    if "saas" in t: return "SaaS"
    return text.strip().title()

def _norm_location(text: str | None) -> str | None:
    if not text: return None
    t = text.lower()
    if "ireland" in t or "dublin" in t: return "Ireland"
    if "uk" in t or "united kingdom" in t or "london" in t: return "UK"
    if "remote" in t: return "Remote"
    return text.strip().title()

def _norm_availability(text: str | None) -> str | None:
    if not text: return None
    t = text.lower()
    if "fractional" in t or "part" in t or "days" in t: return "fractional"
    if "full" in t: return "full_time"
    return text.strip().lower()

def _is_email_like(text: str | None) -> bool:
    return "@" in (text or "") and "." in (text or "")

def _say_and_gather(resp: VoiceResponse, prompt: str, next_step: str, call_sid: str):
    """Helper to say a prompt and gather speech input."""
    if not VoiceResponse or not Gather:
        return resp
    
    state = _init_session(call_sid)
    retries = state["_retries"].get(next_step, 0)

    # Naturalize the prompt with GPT first (safe fallback)
    context = (
        f"Step: {next_step}\n"
        f"State keys: user_type={state.get('user_type')}, name={state.get('name')}, "
        f"role={state.get('role')}, industry={state.get('industry')}, "
        f"location={state.get('location')}, availability={state.get('availability')}"
    )
    natural_prompt = gpt_rephrase(context, prompt)

    tts_path = tts_generate(natural_prompt)
    if not tts_path:
        # Fallback to text-to-speech if TTS unavailable
        resp.say(natural_prompt)
    else:
        full_url = request.url_root[:-1] + tts_path  # absolute URL for Twilio to fetch
        gather = Gather(
            input="speech",
            action=url_for("voice_capture", step=next_step, _external=True),
            method="POST",
            timeout=10,
            speech_timeout="auto",
            language="en-GB",
            speech_model="phone_call"
        )
        gather.play(full_url)
        resp.append(gather)

        if retries == 0:
            # Repeat once to aid recognition & reduce dead-ends
            resp.play(full_url)
            resp.redirect(url_for("voice_capture", step=next_step, _external=True))
            state["_retries"][next_step] = 1
        else:
            resp.say("Moving forward with a default option.")
            state["_retries"][next_step] = 0
    return resp


# -------------------- UTILITIES --------------------
def ok(payload=None, status=200, **extra):
    data = {"ok": True}
    if payload:
        data.update(payload)
    if extra:
        data.update(extra)
    return jsonify(data), status


def bad(message, status=400, **extra):
    data = {"ok": False, "error": message}
    if extra:
        data.update(extra)
    return jsonify(data), status






# -------------------- ROUTES --------------------
@app.route("/", methods=["GET"])
def root_health():
    return "✅ Backend is live!", 200


@app.route("/health", methods=["GET"])
def health():
    """Combined health check for API and voice features."""
    return ok({
        "env": APP_ENV,
        "supabase_connected": bool(supabase),
        "twilio_configured": bool(twilio_client),
        "elevenlabs_configured": bool(ELEVEN_API_KEY and ELEVEN_VOICE_ID),
        "openai_configured": bool(gpt_client),
        "tts_cache_items": len(TTS_CACHE)
    })


@app.route("/matches", methods=["GET"])
def matches():
    """
    Get matches using the match_finder module (Supabase required).
    This endpoint is deprecated - use /match POST instead.
    """
    return bad("This endpoint is deprecated. Use POST /match instead.", 410)


@app.route("/matches/<match_id>", methods=["GET"])
def match_by_id(match_id):
    """
    Get a specific candidate by ID from Supabase.
    """
    try:
        response = supabase.table("executive_profiles").select("*").eq("id", match_id).execute()
        if response.data and len(response.data) > 0:
            return ok({"match": response.data[0]})
        return bad("Match not found", 404)
    except Exception as e:
        print(f"❌ Error fetching match {match_id}:", e)
        return bad(f"Failed to fetch match: {str(e)}", 500)


@app.route("/match", methods=["POST"])
def match():
    try:
        data = request.get_json(force=True, silent=True) or {}
        required = ["industry", "expertise", "availability", "min_experience", "max_salary", "location"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return bad(f"Missing or invalid data for: {', '.join(missing)}")

        try:
            min_experience = int(data["min_experience"])
            max_salary = int(data["max_salary"])
        except Exception:
            return bad("min_experience and max_salary must be numbers.")

        result = find_best_match(
            data["industry"],
            data["expertise"],
            data["availability"],
            min_experience,
            max_salary,
            data["location"],
        )

        if result:
            return ok({
                "message": f"We recommend {result['name']}: {result['summary']}",
                "match": result
            })
        else:
            return ok({"message": "No match found yet. We'll follow up with suggestions soon.", "match": None})

    except Exception as e:
        print("❌ /match error:", e)
        return bad(str(e), 500)


@app.route("/send_intro", methods=["POST"])
def send_intro():
    """
    Legacy endpoint for sending intro emails.
    NOTE: This endpoint is deprecated. Use /request-intro instead.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        client_name = data.get("client_name")
        match_name = data.get("match_name")
        client_email = data.get("email") or data.get("client_email")
        candidate_email = data.get("candidate_email")

        if not client_name or not match_name or not client_email:
            return bad("Missing required fields: client_name, match_name, email/client_email")

        # If candidate_email not provided, try to fetch from match_id or use placeholder
        if not candidate_email and data.get("match_id"):
            try:
                cand_response = supabase.table("executive_profiles").select("email, contact_email").eq("id", data.get("match_id")).execute()
                if cand_response.data and len(cand_response.data) > 0:
                    candidate_email = cand_response.data[0].get("email") or cand_response.data[0].get("contact_email")
            except Exception as e:
                print(f"⚠️ Could not fetch candidate email: {e}")

        if not candidate_email:
            candidate_email = "candidate@example.com"  # Fallback

        print(f"🚀 Sending intro: {client_name} ↔ {match_name} → {client_email}")
        success = send_intro_email(
            client_name=client_name,
            client_email=client_email,
            candidate_name=match_name,
            candidate_email=candidate_email,
            user_type=data.get("user_type", "client"),
            match_id=data.get("match_id")
        )
        return ok({"status": "success" if success else "fail"}, status=200 if success else 500)

    except Exception as e:
        print("❌ /send_intro error:", e)
        return bad(str(e), 500)


@app.route("/request-intro", methods=["POST"])
def request_intro():
    """
    Stores an intro request in Supabase and optionally sends an email.
    Body (JSON):
      {
        "user_type": "client" | "candidate",
        "requester_name": "Jane Doe",
        "requester_email": "jane@acme.com",
        "requester_company": "Acme",
        "match_id": "cand-001",
        "notes": "Series B GTM help"
      }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        required = ["user_type", "requester_name", "requester_email", "match_id"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return bad(f"Missing required fields: {', '.join(missing)}")

        created = datetime.utcnow().isoformat() + "Z"
        record = {
            "user_type": data["user_type"],
            "requester_name": data["requester_name"],
            "requester_email": data["requester_email"],
            "requester_company": data.get("requester_company"),
            "match_id": data["match_id"],
            "status": "pending",
            "notes": data.get("notes"),
            "created_at": created,
        }

        # Store intro request in Supabase
        try:
            res = supabase.table("intros").insert(record).execute()
            intro_id = res.data[0].get("id") if getattr(res, "data", None) else None
        except Exception as e:
            print(f"❌ Supabase insert failed (intros): {e}")
            return bad(f"Failed to store intro request: {str(e)}", 500)

        # Optional confirmation email to the requester
        # TODO: turn on confirmation email
        # try:
        #     # Fetch candidate name from Supabase
        #     cand_response = supabase.table("executive_profiles").select("first_name, last_name").eq("id", data["match_id"]).execute()
        #     cand_name = data["match_id"]  # default if not found
        #     if cand_response.data and len(cand_response.data) > 0:
        #         cand = cand_response.data[0]
        #         first = cand.get("first_name", "")
        #         last = cand.get("last_name", "")
        #         cand_name = " ".join([p for p in [first, last] if p]).strip() or cand_name
        #     send_intro_email(data["requester_name"], cand_name, data["requester_email"])
        # except Exception as e:
        #     print(f"⚠️ send_intro_email failed (non-fatal): {e}")

        payload = {"intro_id": intro_id, "intro": record}
        return ok(payload)

    except Exception as e:
        print("❌ /request-intro error:", e)
        return bad(str(e), 500)


@app.route("/feedback", methods=["POST"])
def feedback():
    """
    Inserts feedback into Supabase using schema:
      user_name / match_name / feedback_text
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        user = data.get("user") or data.get("user_name")
        match = data.get("match") or data.get("match_name")
        fb = data.get("feedback") or data.get("feedback_text")

        if not all([user, match, fb]):
            return bad("Missing required fields: user/user_name, match/match_name, feedback/feedback_text")

        record = {
            "user_name": user,
            "match_name": match,
            "feedback_text": fb,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        supabase.table("feedback").insert(record).execute()
        print("✅ Feedback saved to Supabase.")
        return ok({"status": "saved"})

    except Exception as e:
        print(f"❌ /feedback error: {e}")
        return bad(f"Failed to save feedback: {str(e)}", 500)


@app.route("/post-role", methods=["POST"])
def post_role():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print("🚀 /post-role payload:", data)

        # Only require truly essential fields
        required_fields = [
            "role_title", "industry", "role_description",
            "experience_level", "commitment", "role_type"
        ]
        missing = [f for f in required_fields if f not in data or not data.get(f)]
        if missing:
            return bad(f"Missing required fields: {', '.join(missing)}")

        # Helper to clean optional fields (convert "Not Specified"/"Not Provided" to None)
        def clean_optional(value):
            if not value or value in ["Not Specified", "Not Provided", ""]:
                return None
            return value

        # Prepare Supabase payload with all fields
        supabase_payload = {
            "role_title": data["role_title"],
            "company_name": clean_optional(data.get("company_name")),
            "industry": data["industry"],
            "role_description": data["role_description"],
            "experience_level": data["experience_level"],
            "commitment_type": data["commitment"],
            "is_remote": data.get("is_remote", False),
            "location": clean_optional(data.get("location")),
            "compensation": clean_optional(data.get("budget_range")),
            "role_type": data["role_type"],
            "contact_name": clean_optional(data.get("contact_name")),
            "contact_email": clean_optional(data.get("contact_email")),
            "phone": clean_optional(data.get("phone")),
            "linkedin": clean_optional(data.get("linkedin")),
            "website": clean_optional(data.get("website")),
            "company_mission": clean_optional(data.get("company_mission")),
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        # Save to Supabase
        try:
            supabase.table("role_postings").insert(supabase_payload).execute()
            print("✅ Saved to Supabase (role_postings).")
        except Exception as e:
            print(f"❌ Supabase insert failed (role_postings): {e}")
            return bad(f"Failed to save role posting: {str(e)}", 500)

        return ok({"message": "Role posted successfully!"}, status=201)

    except Exception as e:
        print("❌ /post-role error:", e)
        return bad(str(e), 500)


@app.route("/view-roles", methods=["GET"])
def view_roles():
    """
    Retrieve all role postings from Supabase.
    """
    try:
        response = supabase.table("role_postings").select("*").order("created_at", desc=True).execute()
        roles = response.data or []
        return ok({"roles": roles})
    except Exception as e:
        print(f"❌ /view-roles error: {e}")
        return bad(f"Failed to fetch role postings: {str(e)}", 500)


# -------------------- VOICE ROUTES (Ai-dan Telephony) --------------------
@app.route("/call_candidate", methods=["POST", "OPTIONS"])
@cross_origin()
def call_candidate():
    """
    Initiate an outbound Twilio call to a candidate/client.
    Body (JSON): { "phone": "+1234567890" }
    """
    if request.method == "OPTIONS":
        # Preflight OK
        return jsonify({"status": "ok"}), 200

    if not twilio_client:
        return bad("Twilio not configured. Voice features unavailable.", 503)

    data = request.get_json(silent=True) or {}
    phone = data.get("phone")
    print("DEBUG Incoming phone:", phone)

    if not phone:
        return bad("Phone number required", 400)

    try:
        call = twilio_client.calls.create(
            to=phone,
            from_=TWILIO_PHONE,
            url=url_for("voice_intro", _external=True)
        )
        print("DEBUG Call SID:", call.sid)
        return ok({"status": "calling", "sid": call.sid})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return bad(str(e), 500)


@app.route("/voice/intro", methods=["POST", "GET"])
def voice_intro():
    """
    Entry point for Twilio voice calls (IVR start).
    This is called as a webhook by Twilio when a call is initiated.
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503

    call_sid = request.values.get("CallSid") or "unknown"
    _init_session(call_sid)

    resp = VoiceResponse()
    prompt = "Hi, I'm Ai-dan, your advisor at ExecFlex. Let's keep this simple. Are you hiring for a role, or are you a candidate looking for opportunities?"
    return Response(str(_say_and_gather(resp, prompt, "user_type", call_sid)), mimetype="text/xml")


@app.route("/voice/capture", methods=["POST", "GET"])
def voice_capture():
    """
    Handles speech recognition and conversation flow during voice calls.
    This is called as a webhook by Twilio after gathering speech input.
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503

    call_sid = request.values.get("CallSid") or "unknown"
    step = request.args.get("step", "user_type")

    speech = (request.values.get("SpeechResult") or "").strip()
    confidence = request.values.get("Confidence", "n/a")
    print(f"DEBUG SpeechResult (step={step}): '{speech}' (confidence={confidence})")

    state = _init_session(call_sid)
    resp = VoiceResponse()

    if step == "user_type":
        st = speech.lower()
        state["user_type"] = "client" if "hir" in st or "client" in st else "candidate"
        return Response(str(_say_and_gather(resp, "Great, thanks. What's your first name?", "name", call_sid)), mimetype="text/xml")

    if step == "name":
        state["name"] = speech or "there"
        return Response(str(_say_and_gather(resp, f"Nice to meet you, {state['name']}. Which leadership role are you focused on — for example CFO, CEO, or CTO?", "role", call_sid)), mimetype="text/xml")

    if step == "role":
        state["role"] = _norm_role(speech) or "CFO"
        return Response(str(_say_and_gather(resp, f"Got it, {state['role']}. And which industry are you most focused on — like fintech, insurance, or SaaS?", "industry", call_sid)), mimetype="text/xml")

    if step == "industry":
        state["industry"] = _norm_industry(speech) or "Fintech"
        return Response(str(_say_and_gather(resp, "Perfect. Do you have a location preference — Ireland, the UK, or would remote work?", "location", call_sid)), mimetype="text/xml")

    if step == "location":
        state["location"] = _norm_location(speech) or "Ireland"
        return Response(str(_say_and_gather(resp, "Okay. And do you see this role as fractional — a few days a week — or full time?", "availability", call_sid)), mimetype="text/xml")

    if step == "availability":
        state["availability"] = _norm_availability(speech) or "fractional"
        matches = find_best_match(
            industry=state["industry"],
            expertise=state["role"],
            availability=state["availability"],
            min_experience=5,
            max_salary=200000,
            location=state["location"]
        )
        if matches:
            match = matches[0]
            state["__match"] = match
            pitch = (
                f"Based on what you've told me, I have someone in mind. "
                f"I recommend {match.get('name','an executive')}, a {match.get('role','leader')} in {match.get('location','unknown')}. "
                "Would you like me to make a warm email introduction?"
            )
            # (Optional) pre-cache the scripted pitch. GPT will still rephrase inside _say_and_gather.
            try:
                tts_generate(pitch)
            except Exception as _e:
                print("DEBUG pitch pre-cache failed (safe to ignore):", _e)
            return Response(str(_say_and_gather(resp, pitch, "confirm_intro", call_sid)), mimetype="text/xml")
        else:
            resp.say("Thanks. I don't have a perfect match right now, but I can follow up soon. Goodbye.")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")

    if step == "confirm_intro":
        if _yn(speech):
            return Response(str(_say_and_gather(resp, "Perfect. What's the best email address for me to send the introduction to?", "email", call_sid)), mimetype="text/xml")
        else:
            resp.say("No problem. You can always ask me for more profiles later. Goodbye.")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")

    if step == "email":
        text = speech.replace(" at ", "@").replace(" dot ", ".").replace(" underscore ", "_").replace(" dash ", "-").replace(" ", "")
        state["email"] = text if _is_email_like(text) else "demo@example.com"

        match = state.get("__match") or {}
        # Fetch candidate email from match data if available
        candidate_email = match.get("email") or match.get("contact_email")
        if not candidate_email and match.get("id"):
            # Try to fetch from Supabase
            try:
                cand_response = supabase.table("executive_profiles").select("email, contact_email").eq("id", match.get("id")).execute()
                if cand_response.data and len(cand_response.data) > 0:
                    candidate_email = cand_response.data[0].get("email") or cand_response.data[0].get("contact_email")
            except Exception as e:
                print(f"⚠️ Could not fetch candidate email: {e}")

        ok_sent = send_intro_email(
            client_name=state["name"] or "there",
            client_email=state["email"],
            candidate_name=match.get("name", "an executive"),
            candidate_email=candidate_email or "candidate@example.com",
            subject=None,
            body_extra=f"Context: role {match.get('role','')} in {match.get('location','')}.",
            candidate_role=match.get("role"),
            candidate_industries=match.get("industries", []),
            requester_company=None,
            user_type=state.get("user_type") or "client",
            match_id=match.get("id")
        )

        if ok_sent:
            resp.say(f"Great. I've emailed the introduction to {state['email']}. Goodbye.")
        else:
            resp.say("I tried to send the email but hit an error. Goodbye.")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")

    resp.say("Sorry, I didn't catch that. Let's try again quickly.")
    resp.redirect(url_for("voice_intro", _external=True))
    return Response(str(resp), mimetype="text/xml")


# -------------------- STARTUP DEBUG INFO --------------------
with app.app_context():
    print("DEBUG Registered routes at startup:")
    for rule in app.url_map.iter_rules():
        print(" -", rule)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
