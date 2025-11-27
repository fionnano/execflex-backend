"""
Voice scheduling conversation flow handling for Emma (scheduling assistant).
This service handles the scheduling-specific conversation flow via Twilio.
"""
from flask import request, url_for, Response
from config.clients import VoiceResponse, Gather
from config.app_config import TWILIO_PHONE_NUMBER
from config.clients import supabase_client
from services.tts_service import generate_tts
from services.gpt_service import rephrase
from services.voice_session_service import init_session
from utils.voice_helpers import is_yes, is_email_like


def say_and_gather_scheduling(resp: VoiceResponse, prompt: str, next_step: str, call_sid: str):
    """Helper to say a prompt and gather speech input for scheduling flow."""
    if not VoiceResponse or not Gather:
        return resp
    
    state = init_session(call_sid)
    retries = state["_retries"].get(next_step, 0)

    # Naturalize the prompt with GPT first (safe fallback)
    context = (
        f"Step: {next_step}\n"
        f"State keys: name={state.get('name')}, "
        f"role={state.get('role')}, industry={state.get('industry')}, "
        f"time_preference={state.get('time_preference')}, "
        f"meeting_objective={state.get('meeting_objective')}, "
        f"email={state.get('email')}"
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
            action=url_for("voice.voice_scheduling_capture", step=next_step, _external=True),
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
            resp.redirect(url_for("voice.voice_scheduling_capture", step=next_step, _external=True))
            state["_retries"][next_step] = 1
        else:
            resp.say("Moving forward with a default option.")
            state["_retries"][next_step] = 0
    return resp


def handle_scheduling_step(step: str, speech: str, call_sid: str, executive_id: str = None, executive_name: str = None, executive_expertise: str = None) -> Response:
    """
    Handle a scheduling conversation step based on the current step and speech input.
    
    Returns:
        TwiML Response object
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    state = init_session(call_sid)
    resp = VoiceResponse()

    # Store executive info in session if provided
    if executive_id:
        state["executive_id"] = executive_id
    if executive_name:
        state["executive_name"] = executive_name
    if executive_expertise:
        state["executive_expertise"] = executive_expertise

    if step == "intro":
        # Opening greeting
        exec_name = state.get("executive_name") or "our executive advisor"
        exec_expertise = state.get("executive_expertise")
        expertise_text = f", who brings extensive expertise in {exec_expertise}" if exec_expertise else ""
        
        opening = (
            f"Hello! I'm Emma, your dedicated executive matching assistant. "
            f"I'll be helping you schedule a consultation with {exec_name}{expertise_text}. "
            f"I understand your time is valuable, and I'm here to ensure we find the perfect slot for a productive discussion. "
            f"What's your first name?"
        )
        return Response(
            str(say_and_gather_scheduling(resp, opening, "name", call_sid)),
            mimetype="text/xml"
        )

    if step == "name":
        state["name"] = speech or "there"
        return Response(
            str(say_and_gather_scheduling(
                resp,
                f"Nice to meet you, {state['name']}. What's your current role and industry?",
                "role_industry",
                call_sid
            )),
            mimetype="text/xml"
        )

    if step == "role_industry":
        # Store both role and industry (simple parsing)
        state["role"] = speech or "executive"
        state["industry"] = speech or "business"
        return Response(
            str(say_and_gather_scheduling(
                resp,
                f"Thank you. What are your scheduling preferences? Do you prefer morning or afternoon meetings?",
                "time_preference",
                call_sid
            )),
            mimetype="text/xml"
        )

    if step == "time_preference":
        state["time_preference"] = speech or "flexible"
        return Response(
            str(say_and_gather_scheduling(
                resp,
                f"Got it. What's the main objective for this meeting? What would you like to discuss?",
                "meeting_objective",
                call_sid
            )),
            mimetype="text/xml"
        )

    if step == "meeting_objective":
        state["meeting_objective"] = speech or "general consultation"
        return Response(
            str(say_and_gather_scheduling(
                resp,
                f"Perfect. I can help schedule a {state.get('time_preference', 'flexible')} meeting for {state.get('meeting_objective', 'your consultation')}. "
                f"What's the best email address to send the calendar invitation and meeting details?",
                "email",
                call_sid
            )),
            mimetype="text/xml"
        )

    if step == "email":
        # Normalize email from speech
        text = speech.replace(" at ", "@").replace(" dot ", ".").replace(" underscore ", "_").replace(" dash ", "-").replace(" ", "")
        state["email"] = text if is_email_like(text) else None
        
        if not state["email"]:
            # Ask again if email doesn't look valid
            return Response(
                str(say_and_gather_scheduling(
                    resp,
                    "I didn't catch a valid email address. Could you please repeat it?",
                    "email",
                    call_sid
                )),
                mimetype="text/xml"
            )

        # Store scheduling request in Supabase
        try:
            scheduling_data = {
                "executive_id": state.get("executive_id"),
                "caller_name": state.get("name"),
                "caller_email": state["email"],
                "caller_role": state.get("role"),
                "caller_industry": state.get("industry"),
                "time_preference": state.get("time_preference"),
                "meeting_objective": state.get("meeting_objective"),
                "call_sid": call_sid,
                "status": "scheduled"
            }
            
            # Try to insert into a scheduling table (create if doesn't exist)
            try:
                supabase_client.table("call_scheduling").insert(scheduling_data).execute()
            except Exception as e:
                # Table might not exist, log it
                print(f"⚠️ Could not save scheduling data to database: {e}")
                print(f"   Scheduling data: {scheduling_data}")
        except Exception as e:
            print(f"⚠️ Error saving scheduling request: {e}")

        # Success message
        resp.say(
            f"Excellent, {state['name']}. I've noted your preferences and will send a calendar invitation to {state['email']} shortly. "
            f"We'll find the perfect time slot for your {state.get('meeting_objective', 'consultation')}. "
            f"Thank you for your time, and I look forward to connecting you with {state.get('executive_name', 'our executive')}. Have a wonderful day!"
        )
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")

    # Fallback for unknown steps
    resp.say("Sorry, I didn't catch that. Let's try again.")
    resp.redirect(url_for("voice.voice_scheduling_intro", _external=True))
    return Response(str(resp), mimetype="text/xml")

