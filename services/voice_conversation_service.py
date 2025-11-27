"""
Voice conversation flow handling for Ai-dan.
"""
from flask import request, url_for, Response
from config.clients import VoiceResponse, Gather
from config.app_config import TWILIO_PHONE_NUMBER
from config.clients import supabase_client
from services.tts_service import generate_tts
from services.gpt_service import rephrase
from services.voice_session_service import init_session
from utils.voice_helpers import (
    is_yes, normalize_role, normalize_industry,
    normalize_location, normalize_availability, is_email_like
)
from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email


def say_and_gather(resp: VoiceResponse, prompt: str, next_step: str, call_sid: str):
    """Helper to say a prompt and gather speech input."""
    if not VoiceResponse or not Gather:
        return resp
    
    state = init_session(call_sid)
    retries = state["_retries"].get(next_step, 0)

    # Naturalize the prompt with GPT first (safe fallback)
    context = (
        f"Step: {next_step}\n"
        f"State keys: user_type={state.get('user_type')}, name={state.get('name')}, "
        f"role={state.get('role')}, industry={state.get('industry')}, "
        f"location={state.get('location')}, availability={state.get('availability')}"
    )
    natural_prompt = rephrase(context, prompt)

    tts_path = generate_tts(natural_prompt)
    if not tts_path:
        # Fallback to text-to-speech if TTS unavailable
        resp.say(natural_prompt)
    else:
        full_url = request.url_root[:-1] + tts_path  # absolute URL for Twilio to fetch
        gather = Gather(
            input="speech",
            action=url_for("voice.voice_capture", step=next_step, _external=True),
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
            resp.redirect(url_for("voice.voice_capture", step=next_step, _external=True))
            state["_retries"][next_step] = 1
        else:
            resp.say("Moving forward with a default option.")
            state["_retries"][next_step] = 0
    return resp


def handle_conversation_step(step: str, speech: str, call_sid: str) -> Response:
    """
    Handle a conversation step based on the current step and speech input.
    
    Returns:
        TwiML Response object
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    state = init_session(call_sid)
    resp = VoiceResponse()

    if step == "user_type":
        st = speech.lower()
        state["user_type"] = "client" if "hir" in st or "client" in st else "candidate"
        return Response(
            str(say_and_gather(resp, "Great, thanks. What's your first name?", "name", call_sid)),
            mimetype="text/xml"
        )

    if step == "name":
        state["name"] = speech or "there"
        return Response(
            str(say_and_gather(
                resp,
                f"Nice to meet you, {state['name']}. Which leadership role are you focused on — for example CFO, CEO, or CTO?",
                "role",
                call_sid
            )),
            mimetype="text/xml"
        )

    if step == "role":
        state["role"] = normalize_role(speech) or "CFO"
        return Response(
            str(say_and_gather(
                resp,
                f"Got it, {state['role']}. And which industry are you most focused on — like fintech, insurance, or SaaS?",
                "industry",
                call_sid
            )),
            mimetype="text/xml"
        )

    if step == "industry":
        state["industry"] = normalize_industry(speech) or "Fintech"
        return Response(
            str(say_and_gather(
                resp,
                "Perfect. Do you have a location preference — Ireland, the UK, or would remote work?",
                "location",
                call_sid
            )),
            mimetype="text/xml"
        )

    if step == "location":
        state["location"] = normalize_location(speech) or "Ireland"
        return Response(
            str(say_and_gather(
                resp,
                "Okay. And do you see this role as fractional — a few days a week — or full time?",
                "availability",
                call_sid
            )),
            mimetype="text/xml"
        )

    if step == "availability":
        state["availability"] = normalize_availability(speech) or "fractional"
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
            # Pre-cache the scripted pitch
            try:
                generate_tts(pitch)
            except Exception as _e:
                print("DEBUG pitch pre-cache failed (safe to ignore):", _e)
            return Response(
                str(say_and_gather(resp, pitch, "confirm_intro", call_sid)),
                mimetype="text/xml"
            )
        else:
            resp.say("Thanks. I don't have a perfect match right now, but I can follow up soon. Goodbye.")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")

    if step == "confirm_intro":
        if is_yes(speech):
            return Response(
                str(say_and_gather(
                    resp,
                    "Perfect. What's the best email address for me to send the introduction to?",
                    "email",
                    call_sid
                )),
                mimetype="text/xml"
            )
        else:
            resp.say("No problem. You can always ask me for more profiles later. Goodbye.")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")

    if step == "email":
        text = speech.replace(" at ", "@").replace(" dot ", ".").replace(" underscore ", "_").replace(" dash ", "-").replace(" ", "")
        state["email"] = text if is_email_like(text) else "demo@example.com"

        match = state.get("__match") or {}
        # Fetch candidate email from match data if available
        candidate_email = match.get("email") or match.get("contact_email")
        if not candidate_email and match.get("id"):
            # Try to fetch from Supabase
            try:
                cand_response = supabase_client.table("executive_profiles").select("email, contact_email").eq("id", match.get("id")).execute()
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
    resp.redirect(url_for("voice.voice_intro", _external=True))
    return Response(str(resp), mimetype="text/xml")

