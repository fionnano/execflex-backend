"""
ORPHANED CODE - FOR REFERENCE ONLY
TODO: DELETE THIS FILE
Company scheduling conversation flow for Emma (scheduling assistant).
This service handles outbound calls to companies/clients to schedule consultations with executives.
Uses ChatGPT for interactive conversations and ElevenLabs for natural TTS.
"""
from flask import request, url_for, Response
from config.clients import VoiceResponse, Gather
from config.clients import supabase_client
from services.tts_service import generate_tts
from services.gpt_service import generate_conversational_response
from services.voice_session_service import init_session
from utils.voice_helpers import is_yes, is_email_like


# System prompt for Emma (scheduling assistant)
EMMA_SYSTEM_PROMPT = """You are Emma, an empathetic and sophisticated AI scheduling assistant for ExecFlex, an executive matching platform.

Core Personality Traits:
- Warmth: Show genuine interest in helping while maintaining professionalism
- Empathy: Understand and acknowledge scheduling challenges
- Efficiency: Keep conversations focused while being thorough
- Adaptability: Adjust communication style to match the caller
- Proactivity: Anticipate needs and offer solutions
- Confidence: Demonstrate expertise in handling executive scheduling

Your role is to help schedule a consultation between a company/client and an executive. Be natural, conversational, and guide the conversation to collect:
1. Caller's name and role
2. Company information (size, stage, industry)
3. Scheduling preferences (time of day, timezone, flexibility)
4. Meeting objectives and what they hope to achieve
5. Any special requirements or preferences
6. Email address for calendar invitation

Keep responses concise (1-2 sentences) and natural. Ask one question at a time."""


def say_and_gather_scheduling(resp: VoiceResponse, prompt: str, next_step: str, call_sid: str, use_gpt: bool = True):
    """Helper to say a prompt and gather speech input for scheduling flow."""
    if not VoiceResponse or not Gather:
        return resp
    
    state = init_session(call_sid)
    retries = state["_retries"].get(next_step, 0)

    # Use GPT to make the prompt more natural if enabled
    if use_gpt:
        conversation_history = state.get("_conversation_history", [])
        context = {
            "executive_name": state.get("executive_name"),
            "executive_expertise": state.get("executive_expertise"),
            "caller_name": state.get("name"),
            "step": next_step
        }
        
        gpt_response = generate_conversational_response(
            system_prompt=EMMA_SYSTEM_PROMPT,
            conversation_history=conversation_history,
            user_input="",  # No user input, just generating next prompt
            context=context,
            temperature=0.8,
            max_tokens=100
        )
        
        # Use GPT response if available, otherwise use original prompt
        natural_prompt = gpt_response if gpt_response else prompt
    else:
        natural_prompt = prompt

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


def update_conversation_history(call_sid: str, role: str, content: str):
    """Update conversation history for GPT context."""
    state = init_session(call_sid)
    if "_conversation_history" not in state:
        state["_conversation_history"] = []
    state["_conversation_history"].append({"role": role, "content": content})
    # Keep last 10 messages to avoid token limits
    if len(state["_conversation_history"]) > 10:
        state["_conversation_history"] = state["_conversation_history"][-10:]


def handle_scheduling_step(step: str, speech: str, call_sid: str, executive_id: str = None, executive_name: str = None, executive_expertise: str = None) -> Response:
    """
    Handle a scheduling conversation step with interactive GPT responses.
    
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

    # Update conversation history with user input
    if speech:
        update_conversation_history(call_sid, "user", speech)

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
        
        update_conversation_history(call_sid, "assistant", opening)
        return Response(
            str(say_and_gather_scheduling(resp, opening, "name", call_sid, use_gpt=False)),
            mimetype="text/xml"
        )

    if step == "name":
        state["name"] = speech or "there"
        prompt = f"Nice to meet you, {state['name']}. What's your current role and what company do you work for?"
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather_scheduling(resp, prompt, "role_company", call_sid)),
            mimetype="text/xml"
        )

    if step == "role_company":
        state["role"] = speech or ""
        # Use GPT to extract company info and ask follow-up
        gpt_response = generate_conversational_response(
            system_prompt=EMMA_SYSTEM_PROMPT,
            conversation_history=state.get("_conversation_history", []),
            user_input=speech,
            context={"step": "role_company", "caller_name": state.get("name")},
            temperature=0.8,
            max_tokens=120
        )
        
        if gpt_response:
            # GPT might ask about company size or other details
            prompt = gpt_response
        else:
            prompt = f"Thank you. Can you tell me a bit about your company? What size is it, and what stage are you at?"
        
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather_scheduling(resp, prompt, "company_info", call_sid)),
            mimetype="text/xml"
        )

    if step == "company_info":
        state["company_info"] = speech or ""
        prompt = "Great. What are your scheduling preferences? Do you prefer morning or afternoon meetings, and what timezone are you in?"
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather_scheduling(resp, prompt, "time_preference", call_sid)),
            mimetype="text/xml"
        )

    if step == "time_preference":
        state["time_preference"] = speech or "flexible"
        prompt = "Perfect. What's the main objective for this meeting? What would you like to discuss with the executive?"
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather_scheduling(resp, prompt, "meeting_objective", call_sid)),
            mimetype="text/xml"
        )

    if step == "meeting_objective":
        state["meeting_objective"] = speech or "general consultation"
        
        # Use GPT to generate a natural follow-up
        gpt_response = generate_conversational_response(
            system_prompt=EMMA_SYSTEM_PROMPT,
            conversation_history=state.get("_conversation_history", []),
            user_input=speech,
            context={
                "step": "meeting_objective",
                "caller_name": state.get("name"),
                "executive_name": state.get("executive_name")
            },
            temperature=0.8,
            max_tokens=120
        )
        
        if gpt_response:
            prompt = gpt_response + " What's the best email address to send the calendar invitation and meeting details?"
        else:
            prompt = f"Excellent. I've noted your preferences. What's the best email address to send the calendar invitation and meeting details?"
        
        update_conversation_history(call_sid, "assistant", prompt)
        return Response(
            str(say_and_gather_scheduling(resp, prompt, "email", call_sid)),
            mimetype="text/xml"
        )

    if step == "email":
        # Normalize email from speech
        text = speech.replace(" at ", "@").replace(" dot ", ".").replace(" underscore ", "_").replace(" dash ", "-").replace(" ", "")
        state["email"] = text if is_email_like(text) else None
        
        if not state["email"]:
            # Ask again if email doesn't look valid
            prompt = "I didn't catch a valid email address. Could you please repeat it?"
            update_conversation_history(call_sid, "assistant", prompt)
            return Response(
                str(say_and_gather_scheduling(resp, prompt, "email", call_sid)),
                mimetype="text/xml"
            )

        # Store scheduling request in Supabase
        try:
            scheduling_data = {
                "executive_id": state.get("executive_id"),
                "caller_name": state.get("name"),
                "caller_email": state["email"],
                "caller_role": state.get("role"),
                "company_info": state.get("company_info"),
                "time_preference": state.get("time_preference"),
                "meeting_objective": state.get("meeting_objective"),
                "call_sid": call_sid,
                "status": "scheduled"
            }
            
            # Try to insert into a scheduling table
            try:
                supabase_client.table("call_scheduling").insert(scheduling_data).execute()
                print(f"✅ Saved scheduling request for {state.get('name')} ({state['email']})")
            except Exception as e:
                # Table might not exist, log it
                print(f"⚠️ Could not save scheduling data to database: {e}")
                print(f"   Scheduling data: {scheduling_data}")
        except Exception as e:
            print(f"⚠️ Error saving scheduling request: {e}")

        # Success message
        exec_name = state.get("executive_name") or "our executive"
        closing = (
            f"Excellent, {state['name']}. I've noted all your preferences and will send a calendar invitation to {state['email']} shortly. "
            f"We'll find the perfect time slot for your {state.get('meeting_objective', 'consultation')} with {exec_name}. "
            f"Thank you for your time, and I look forward to connecting you. Have a wonderful day!"
        )
        
        update_conversation_history(call_sid, "assistant", closing)
        resp.say(closing)
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")

    # Fallback for unknown steps
    resp.say("Sorry, I didn't catch that. Let's try again.")
    resp.redirect(url_for("voice.voice_scheduling_intro", _external=True))
    return Response(str(resp), mimetype="text/xml")

