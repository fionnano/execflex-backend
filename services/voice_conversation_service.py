"""
Voice conversation flow handling for Ai-dan.
"""
from flask import request, url_for, Response
from config.clients import VoiceResponse, Gather
from config.app_config import TWILIO_PHONE_NUMBER
from config.clients import supabase_client
from services.tts_service import generate_tts
from services.gpt_service import rephrase, generate_conversational_response
from services.voice_session_service import init_session
from utils.voice_helpers import (
    is_yes, normalize_role, normalize_industry,
    normalize_location, normalize_availability, is_email_like
)
from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email

# System prompt for Ai-dan (executive matching assistant)
AI_DAN_SYSTEM_PROMPT = """You are Ai-dan, a friendly and efficient executive search consultant at ExecFlex.

Your role is to help executives list themselves for work opportunities or help companies find executives. Be natural, conversational, and guide the conversation efficiently.

Keep responses concise (1-2 sentences) and friendly. Ask one question at a time."""


def update_conversation_history(call_sid: str, role: str, content: str, step: str = None, user_id: str = None, opportunity_id: str = None, thread_id: str = None):
    """Update conversation history for GPT context (in-memory and database)."""
    from services.voice_session_service import init_session
    from config.clients import supabase_client
    from datetime import datetime
    
    # Update in-memory session (for immediate GPT context)
    state = init_session(call_sid)
    if "_conversation_history" not in state:
        state["_conversation_history"] = []
    state["_conversation_history"].append({"role": role, "content": content})
    # Keep last 10 messages to avoid token limits
    if len(state["_conversation_history"]) > 10:
        state["_conversation_history"] = state["_conversation_history"][-10:]
    
    # Also persist to database interactions table for analytics
    # We need a thread_id to store interactions - create or find one
    try:
        if supabase_client:
            # Get or create thread for this call
            if not thread_id:
                # Try to find existing thread for this call_sid
                if user_id:
                    thread_resp = supabase_client.table("threads").select("id").eq("primary_user_id", user_id).eq("active", True).order("created_at", {"ascending": False}).limit(1).execute()
                    if thread_resp.data and len(thread_resp.data) > 0:
                        thread_id = thread_resp.data[0].get("id")
                    else:
                        # Create new thread for this call
                        thread_payload = {
                            "primary_user_id": user_id,
                            "subject": f"Voice call: {call_sid}",
                            "status": "open",
                            "opportunity_id": opportunity_id,
                            "active": True
                        }
                        thread_resp = supabase_client.table("threads").insert(thread_payload).execute()
                        if thread_resp.data and len(thread_resp.data) > 0:
                            thread_id = thread_resp.data[0].get("id")
                            state["__thread_id"] = thread_id  # Store in session for future calls
            
            if thread_id:
                # Store interaction record
                # For voice calls, we accumulate transcript and create one interaction per call
                # or append to existing interaction. For simplicity, create separate interactions per message step.
                interaction_payload = {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "channel": "voice",
                    "direction": "inbound" if role == "user" else "outbound",
                    "provider": "twilio",
                    "provider_ref": call_sid,
                    "transcript_text": content,
                    "artifacts": {
                        "step": step,
                        "role": role,
                        "call_sid": call_sid
                    },
                    "raw_payload": {
                        "content": content,
                        "step": step
                    }
                }
                supabase_client.table("interactions").insert(interaction_payload).execute()
    except Exception as e:
        print(f"⚠️ Could not save conversation history to database: {e}")


def say_and_gather(resp: VoiceResponse, prompt: str, next_step: str, call_sid: str, use_gpt: bool = True):
    """Helper to say a prompt and gather speech input."""
    if not VoiceResponse or not Gather:
        return resp
    
    state = init_session(call_sid)
    retries = state["_retries"].get(next_step, 0)

    # Use GPT for more natural conversation if enabled
    if use_gpt:
        conversation_history = state.get("_conversation_history", [])
        context = {
            "step": next_step,
            "user_type": state.get("user_type"),
            "name": state.get("name"),
            "role": state.get("role"),
            "industry": state.get("industry"),
            "location": state.get("location"),
            "availability": state.get("availability")
        }
        
        gpt_response = generate_conversational_response(
            system_prompt=AI_DAN_SYSTEM_PROMPT,
            conversation_history=conversation_history,
            user_input="",  # No user input, just generating next prompt
            context=context,
            temperature=0.7,
            max_tokens=100
        )
        
        # Use GPT response if available, otherwise use rephrase fallback
        if gpt_response:
            natural_prompt = gpt_response
        else:
            context_str = (
                f"Step: {next_step}\n"
                f"State keys: user_type={state.get('user_type')}, name={state.get('name')}, "
                f"role={state.get('role')}, industry={state.get('industry')}, "
                f"location={state.get('location')}, availability={state.get('availability')}"
            )
            natural_prompt = rephrase(context_str, prompt)
    else:
        # Fallback to simple rephrase
        context_str = (
            f"Step: {next_step}\n"
            f"State keys: user_type={state.get('user_type')}, name={state.get('name')}, "
            f"role={state.get('role')}, industry={state.get('industry')}, "
            f"location={state.get('location')}, availability={state.get('availability')}"
        )
        natural_prompt = rephrase(context_str, prompt)

    tts_path = generate_tts(natural_prompt)
    if not tts_path:
        # Fallback to text-to-speech if TTS unavailable
        resp.say(natural_prompt)
    else:
        full_url = request.url_root[:-1] + tts_path  # absolute URL for Twilio to fetch
        gather = Gather(
            input="speech",
            action=url_for("voice.voice_inbound", step=next_step, _external=True),
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
            resp.redirect(url_for("voice.voice_inbound", step=next_step, _external=True))
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
        prompt = "Great, thanks. What's your first name?"
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather(resp, prompt, "name", call_sid, use_gpt=False)),
            mimetype="text/xml"
        )

    if step == "name":
        state["name"] = speech or "there"
        if speech:
            update_conversation_history(call_sid, "user", speech)
        prompt = f"Nice to meet you, {state['name']}. Which leadership role are you focused on — for example CFO, CEO, or CTO?"
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather(resp, prompt, "role", call_sid)),
            mimetype="text/xml"
        )

    if step == "role":
        state["role"] = normalize_role(speech) or "CFO"
        if speech:
            update_conversation_history(call_sid, "user", speech)
        prompt = f"Got it, {state['role']}. And which industry are you most focused on — like fintech, insurance, or SaaS?"
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather(resp, prompt, "industry", call_sid)),
            mimetype="text/xml"
        )

    if step == "industry":
        state["industry"] = normalize_industry(speech) or "Fintech"
        if speech:
            update_conversation_history(call_sid, "user", speech)
        prompt = "Perfect. Do you have a location preference — Ireland, the UK, or would remote work?"
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather(resp, prompt, "location", call_sid)),
            mimetype="text/xml"
        )

    if step == "location":
        state["location"] = normalize_location(speech) or "Ireland"
        if speech:
            update_conversation_history(call_sid, "user", speech)
        prompt = "Okay. And do you see this role as fractional — a few days a week — or full time?"
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather(resp, prompt, "availability", call_sid)),
            mimetype="text/xml"
        )

    if step == "availability":
        state["availability"] = normalize_availability(speech) or "fractional"
        if speech:
            update_conversation_history(call_sid, "user", speech)
        
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
            
            # Use GPT to generate a natural pitch
            context = {
                "step": "availability",
                "match_name": match.get('name', 'an executive'),
                "match_role": match.get('role', 'leader'),
                "match_location": match.get('location', 'unknown')
            }
            gpt_pitch = generate_conversational_response(
                system_prompt=AI_DAN_SYSTEM_PROMPT,
                conversation_history=state.get("_conversation_history", []),
                user_input="",
                context=context,
                temperature=0.7,
                max_tokens=120
            )
            
            if gpt_pitch:
                pitch = gpt_pitch + " Would you like me to make a warm email introduction?"
            else:
                pitch = (
                    f"Based on what you've told me, I have someone in mind. "
                    f"I recommend {match.get('name','an executive')}, a {match.get('role','leader')} in {match.get('location','unknown')}. "
                    "Would you like me to make a warm email introduction?"
                )
            
            update_conversation_history(call_sid, "assistant", pitch)
            # Pre-cache the pitch
            try:
                generate_tts(pitch)
            except Exception as _e:
                print("DEBUG pitch pre-cache failed (safe to ignore):", _e)
            return Response(
                str(say_and_gather(resp, pitch, "confirm_intro", call_sid)),
                mimetype="text/xml"
            )
        else:
            closing = "Thanks. I don't have a perfect match right now, but I can follow up soon. Goodbye."
            update_conversation_history(call_sid, "assistant", closing)
            resp.say(closing)
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")

    if step == "confirm_intro":
        if speech:
            update_conversation_history(call_sid, "user", speech)
        if is_yes(speech):
            prompt = "Perfect. What's the best email address for me to send the introduction to?"
            update_conversation_history(call_sid, "assistant", prompt)
            return Response(
                str(say_and_gather(resp, prompt, "email", call_sid)),
                mimetype="text/xml"
            )
        else:
            closing = "No problem. You can always ask me for more profiles later. Goodbye."
            update_conversation_history(call_sid, "assistant", closing)
            resp.say(closing)
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")

    if step == "email":
        if speech:
            update_conversation_history(call_sid, "user", speech)
        text = speech.replace(" at ", "@").replace(" dot ", ".").replace(" underscore ", "_").replace(" dash ", "-").replace(" ", "")
        state["email"] = text if is_email_like(text) else "demo@example.com"

        match = state.get("__match") or {}
        # Fetch candidate email from match data if available
        candidate_email = match.get("email") or match.get("contact_email")
        if not candidate_email and match.get("id"):
            # Try to fetch from Supabase - match.id is now a people_profiles.id or user_id
            try:
                # First try people_profiles by id
                cand_response = supabase_client.table("people_profiles").select("user_id").eq("id", match.get("id")).limit(1).execute()
                candidate_user_id = None
                if cand_response.data and len(cand_response.data) > 0:
                    candidate_user_id = cand_response.data[0].get("user_id")
                else:
                    # Try as user_id directly
                    candidate_user_id = match.get("id")
                
                if candidate_user_id:
                    # Get email from channel_identities
                    email_response = supabase_client.table("channel_identities").select("value").eq("user_id", candidate_user_id).eq("channel", "email").limit(1).execute()
                    if email_response.data and len(email_response.data) > 0:
                        candidate_email = email_response.data[0].get("value")
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
    # TODO: Update to use inbound endpoint when implemented
    resp.say("Please use the web interface for now.", voice="alice", language="en-GB")
    resp.hangup()
    return Response(str(resp), mimetype="text/xml")

