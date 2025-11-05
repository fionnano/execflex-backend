# app.py
#
# ExecFlex Voice Agent (Ai-dan) using Flask + Twilio + ElevenLabs + GPT rephrase
# FULL version with:
# - Render-compatible port binding
# - /call_candidate route (POST + OPTIONS) with JSON responses
# - Route listing + extra debug logs
# - TTS caching
# - Structured conversation flow
# - Email intro + Supabase logging (via modules/email_sender)
# - Best-match finder (via modules/match_finder)

import os
import uuid
import requests
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify, Response, url_for
from flask_cors import CORS, cross_origin

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather

from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email

# -------------------------------
# NEW: OpenAI (GPT) for natural rephrasing
# -------------------------------
try:
    from openai import OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY".lower()) or os.getenv("openai_api_key")
    gpt_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
    print("DEBUG OPENAI_API_KEY loaded:", (OPENAI_API_KEY[:7] + "…") if OPENAI_API_KEY else None)
except Exception as _e:
    gpt_client = None
    print("⚠️ OpenAI import/init failed; will fall back to scripted prompts.", _e)

def gpt_rephrase(context: str, fallback: str) -> str:
    """
    Use GPT to make Ai-dan’s reply more natural and human.
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

# -------------------------------
# Load .env (explicit path log)
# -------------------------------
env_path = Path(__file__).resolve().parent / ".env"
print("DEBUG Loading .env from:", env_path)
load_dotenv(dotenv_path=env_path)

# -------------------------------
# Flask setup
# -------------------------------
app = Flask(__name__, static_folder="static")
CORS(app, origins="*")

# -------------------------------
# Twilio
# -------------------------------
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE       = os.getenv("TWILIO_PHONE_NUMBER") or os.getenv("TWILIO_PHONE")
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

print("DEBUG TWILIO_ACCOUNT_SID loaded:", (TWILIO_ACCOUNT_SID[:6] + "…") if TWILIO_ACCOUNT_SID else None)
print("DEBUG TWILIO_PHONE_NUMBER loaded:", TWILIO_PHONE)

# -------------------------------
# ElevenLabs
# -------------------------------
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY") or os.getenv("ELEVENLABS_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVEN_VOICE_ID") or os.getenv("ELEVENLABS_VOICE_ID")

print("DEBUG ELEVEN_API_KEY loaded:", ELEVEN_API_KEY[:6] if ELEVEN_API_KEY else None)
print("DEBUG ELEVEN_VOICE_ID loaded:", ELEVEN_VOICE_ID)

# -------------------------------
# TTS + Cache
# -------------------------------
CACHE_DIR = Path("static/audio")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TTS_CACHE = {}  # text -> path

def tts_generate(text: str) -> str:
    """Generate TTS from ElevenLabs (or return cached)."""
    if text in TTS_CACHE:
        return TTS_CACHE[text]

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

    # If supported by your chosen voice:
    # data["voice_style"] = "Conversational"

    print("DEBUG Generating TTS:", text[:80])
    r = requests.post(url, headers=headers, json=data, timeout=60)
    r.raise_for_status()

    with open(filepath, "wb") as f:
        f.write(r.content)

    rel_path = f"/static/audio/{filename}"
    TTS_CACHE[text] = rel_path
    return rel_path

# -------------------------------
# Pre-cache static prompts
# -------------------------------
COMMON_PROMPTS = [
    "Hi, I’m Ai-dan, your advisor at ExecFlex. Let’s keep this simple. Are you hiring for a role, or are you a candidate looking for opportunities?",
    "Great, thanks. What’s your first name?",
    "Nice to meet you. Which leadership role are you focused on — for example CFO, CEO, or CTO?",
    "Got it. And which industry are you most focused on — like fintech, insurance, or SaaS?",
    "Perfect. Do you have a location preference — Ireland, the UK, or would remote work?",
    "Okay. And do you see this role as fractional — a few days a week — or full time?",
    "Based on what you’ve told me, I have someone in mind. Would you like me to make a warm email introduction?",
    "Perfect. What’s the best email address for me to send the introduction to?",
    "Great. I’ve emailed the introduction. Goodbye.",
    "Sorry, I didn’t catch that. Let’s try again quickly."
]

print("DEBUG Pre-caching common prompts...")
for p in COMMON_PROMPTS:
    try:
        tts_generate(p)
    except Exception as e:
        print("⚠️ Could not cache:", p, e)

# -------------------------------
# Session store
# -------------------------------
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

# -------------------------------
# Helpers
# -------------------------------
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

# -------------------------------
# Say + Gather with GPT + cache
# -------------------------------
def _say_and_gather(resp: VoiceResponse, prompt: str, next_step: str, call_sid: str):
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

# -------------------------------
# Outbound call trigger
# -------------------------------
@app.route("/call_candidate", methods=["POST", "OPTIONS"])
@cross_origin()
def call_candidate():
    if request.method == "OPTIONS":
        # Preflight OK
        return jsonify({"status": "ok"}), 200

    data = request.get_json(silent=True) or {}
    phone = data.get("phone")
    print("DEBUG Incoming phone:", phone)

    if not phone:
        return jsonify({"error": "Phone number required"}), 400

    try:
        call = twilio_client.calls.create(
            to=phone,
            from_=TWILIO_PHONE,
            url=url_for("voice_intro", _external=True)  # NOTE: your route is /voice/intro
        )
        print("DEBUG Call SID:", call.sid)
        return jsonify({"status": "calling", "sid": call.sid})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# -------------------------------
# Intro
# -------------------------------
@app.route("/voice/intro", methods=["POST", "GET"])
def voice_intro():
    call_sid = request.values.get("CallSid") or "unknown"
    _init_session(call_sid)

    resp = VoiceResponse()
    prompt = "Hi, I’m Ai-dan, your advisor at ExecFlex. Let’s keep this simple. Are you hiring for a role, or are you a candidate looking for opportunities?"
    return Response(str(_say_and_gather(resp, prompt, "user_type", call_sid)), mimetype="text/xml")

# -------------------------------
# Capture
# -------------------------------
@app.route("/voice/capture", methods=["POST", "GET"])
def voice_capture():
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
        return Response(str(_say_and_gather(resp, "Great, thanks. What’s your first name?", "name", call_sid)), mimetype="text/xml")

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
                f"Based on what you’ve told me, I have someone in mind. "
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
            resp.say("Thanks. I don’t have a perfect match right now, but I can follow up soon. Goodbye.")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")

    if step == "confirm_intro":
        if _yn(speech):
            return Response(str(_say_and_gather(resp, "Perfect. What’s the best email address for me to send the introduction to?", "email", call_sid)), mimetype="text/xml")
        else:
            resp.say("No problem. You can always ask me for more profiles later. Goodbye.")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")

    if step == "email":
        text = speech.replace(" at ", "@").replace(" dot ", ".").replace(" underscore ", "_").replace(" dash ", "-").replace(" ", "")
        state["email"] = text if _is_email_like(text) else "demo@example.com"

        match = state.get("__match") or {}
        ok = send_intro_email(
            client_name=state["name"] or "there",
            client_email=state["email"],
            candidate_name=match.get("name", "an executive"),
            candidate_email=match.get("email") or "candidate@example.com",
            subject=None,
            body_extra=f"Context: role {match.get('role','')} in {match.get('location','')}.",
            candidate_role=match.get("role"),
            candidate_industries=match.get("industries", []),
            requester_company=None,
            user_type=state.get("user_type") or "client",
            match_id=match.get("id")
        )

        if ok:
            resp.say(f"Great. I’ve emailed the introduction to {state['email']}. Goodbye.")
        else:
            resp.say("I tried to send the email but hit an error. Goodbye.")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")

    resp.say("Sorry, I didn’t catch that. Let’s try again quickly.")
    resp.redirect(url_for("voice_intro", _external=True))
    return Response(str(resp), mimetype="text/xml")

# -------------------------------
# Healthcheck
# -------------------------------
@app.route("/health", methods=["GET"])
def health():
    return {
        "ok": True,
        "env": os.getenv("ENV", "dev"),
        "openai": bool(OPENAI_API_KEY),
        "tts_cache_items": len(TTS_CACHE)
    }

# -------------------------------
# Debug: list routes at startup
# -------------------------------
with app.app_context():
    print("DEBUG Registered routes at startup:")
    for rule in app.url_map.iter_rules():
        print(" -", rule)

# -------------------------------
# Healthcheck
# -------------------------------
@app.route("/health", methods=["GET"])
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
