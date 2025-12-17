"""
Qualification Conversation Service - Handles turn-based conversation flow.
Separated from provisioning/onboarding logic.
"""
from typing import Dict, Optional, List, Any, Tuple
from config.clients import VoiceResponse, Gather
from services.qualification_turn_service import (
    get_or_create_interaction_for_call,
    get_next_turn_sequence,
    save_turn,
    get_conversation_turns,
    apply_extracted_updates
)
from services.qualification_agent_service import generate_qualification_response
from services.tts_service import generate_tts
from config.clients import supabase_client
import os
import time
import json
import re


def _timing_enabled() -> bool:
    return os.getenv("VOICE_TIMING_LOG", "0").lower() in ("1", "true", "yes", "y")


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _log_timing(event: str, payload: Dict[str, Any]) -> None:
    if not _timing_enabled():
        return
    try:
        print(json.dumps({"event": event, **payload}, default=str))
    except Exception:
        # Never break call flow due to logging issues
        pass


def _user_wants_to_end_call(user_speech: Optional[str]) -> bool:
    """
    Deterministic early-exit for voice UX.
    If the user says "not now", "stop", "bye", etc, end politely instead of forcing more questions.
    """
    if not user_speech:
        return False
    text = user_speech.strip().lower()
    # Keep this conservative to avoid false positives.
    patterns = [
        r"\bnot now\b",
        r"\bcall me back\b",
        r"\b(can you )?call back\b",
        r"\bi'?m busy\b",
        r"\bbusy right now\b",
        r"\bstop\b",
        r"\bplease stop\b",
        r"\bhang up\b",
        r"\bgoodbye\b",
        r"\bbye\b",
        r"\bno thanks\b",
        r"\bnot interested\b",
    ]
    return any(re.search(p, text) for p in patterns)


def get_call_context(call_sid: str, job_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get context for a call (interaction, user, signup_mode, existing profile).
    
    Returns:
        Dict with: interaction, user_id, thread_id, signup_mode, existing_profile, existing_role
    """
    # Get or create interaction
    interaction = get_or_create_interaction_for_call(call_sid, job_id)
    if not interaction:
        return {}
    
    interaction_id = interaction["id"]
    thread_id = interaction.get("thread_id")
    user_id = interaction.get("user_id")
    
    # Get job to fetch signup_mode
    signup_mode = None
    if job_id:
        try:
            job_resp = supabase_client.table("outbound_call_jobs")\
                .select("artifacts, user_id")\
                .eq("id", job_id)\
                .limit(1)\
                .execute()
            
            if job_resp.data:
                job = job_resp.data[0]
                artifacts = job.get("artifacts", {}) or {}
                signup_mode = artifacts.get("signup_mode")
                user_id = user_id or job.get("user_id")
        except Exception as e:
            print(f"⚠️ Could not fetch job: {e}")
    
    # Get existing profile and role if user_id available
    existing_profile = None
    existing_role = None
    
    if user_id:
        try:
            profile_resp = supabase_client.table("people_profiles")\
                .select("first_name, last_name, headline")\
                .eq("user_id", user_id)\
                .limit(1)\
                .execute()
            
            if profile_resp.data:
                existing_profile = profile_resp.data[0]
            
            role_resp = supabase_client.table("role_assignments")\
                .select("role")\
                .eq("user_id", user_id)\
                .order("confidence", desc=True)\
                .limit(1)\
                .execute()
            
            if role_resp.data:
                existing_role = role_resp.data[0].get("role")
        except Exception as e:
            print(f"⚠️ Could not fetch profile/role: {e}")

    # Backfill signup_mode for personalized opening message:
    # Priority: job artifacts > existing_role > user_preferences
    if not signup_mode and existing_role in ("talent", "hirer"):
        signup_mode = existing_role

    if not signup_mode and user_id:
        try:
            prefs_resp = supabase_client.table("user_preferences")\
                .select("last_mode, default_mode")\
                .eq("user_id", user_id)\
                .limit(1)\
                .execute()
            if prefs_resp.data:
                prefs = prefs_resp.data[0] or {}
                candidate = (prefs.get("last_mode") or prefs.get("default_mode") or "").strip().lower()
                if candidate in ("talent", "hirer"):
                    signup_mode = candidate
        except Exception as e:
            print(f"⚠️ Could not fetch user_preferences for signup_mode: {e}")
    
    return {
        "interaction": interaction,
        "interaction_id": interaction_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "signup_mode": signup_mode,
        "existing_profile": existing_profile,
        "existing_role": existing_role
    }


def generate_opening_message(signup_mode: Optional[str] = None) -> str:
    """
    Generate the opening message based on signup_mode.
    
    Returns:
        Opening message text
    """
    # Normalize signup_mode
    if signup_mode in ("talent", "job_seeker", "executive", "candidate"):
        return (
            "Hi, this is A I Dan from ExecFlex. I noticed you just logged in looking for executive opportunities. "
            "Have I caught you at a bad time?"
        )
    elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
        return (
            "Hello, this is A I Dan from ExecFlex. I noticed you just logged in looking for executive talent for your organization. "
            "Have I caught you at a bad time?"
        )
    else:
        # Unknown signup_mode - ask to clarify with clear distinction
        return (
            "Hello, this is A I Dan from ExecFlex. I noticed you just logged in and I wasn't sure "
            "if you are looking for executive talent for your organization, or if you are an executive looking for job opportunities. "
            "Is this a bad time to talk?"
        )


def handle_conversation_turn(
    call_sid: str,
    user_speech: Optional[str] = None,
    speech_confidence: Optional[str] = None,
    job_id: Optional[str] = None,
    request_url_root: Optional[str] = None
) -> Tuple[Optional[VoiceResponse], Optional[str]]:
    """
    Handle a single conversation turn.
    
    Args:
        call_sid: Twilio CallSid
        user_speech: User's spoken response (None for opening turn)
        speech_confidence: Speech recognition confidence
        job_id: Optional job ID for context
        request_url_root: Base URL for generating audio URLs
        
    Returns:
        Tuple of (TwiML VoiceResponse, error_message)
        If error_message is not None, VoiceResponse will be None
    """
    if not VoiceResponse or not Gather:
        return None, "Voice features not available"

    total_start = time.perf_counter()
    timings: Dict[str, int] = {}
    
    # Get call context
    try:
        t0 = time.perf_counter()
        context = get_call_context(call_sid, job_id)
        timings["get_call_context_ms"] = _ms_since(t0)
        if not context.get("interaction"):
            print(f"⚠️ Could not get/create interaction for call {call_sid}, job_id={job_id}")
            return None, f"Could not get/create interaction for call {call_sid}"
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Exception in get_call_context: {e}")
        return None, f"Error getting call context: {str(e)}"
    
    interaction_id = context["interaction_id"]
    thread_id = context["thread_id"]
    user_id = context["user_id"]
    signup_mode = context["signup_mode"]
    existing_profile = context["existing_profile"]
    existing_role = context["existing_role"]
    
    resp = VoiceResponse()
    
    # Treat "no speech" carefully:
    # - Initial call webhook has no SpeechResult -> opening message
    # - Mid-call Gather timeout also produces no SpeechResult -> we must NOT restart
    next_seq_peek = None
    if not user_speech:
        try:
            t0 = time.perf_counter()
            next_seq_peek = get_next_turn_sequence(interaction_id)
            timings["get_next_turn_sequence_peek_ms"] = _ms_since(t0)
        except Exception:
            next_seq_peek = None

    # Opening only if there are no existing turns yet.
    is_first_turn = (next_seq_peek == 1) if next_seq_peek is not None else False

    if not user_speech and is_first_turn:
        t0 = time.perf_counter()
        opening_message = generate_opening_message(signup_mode)
        timings["generate_opening_message_ms"] = _ms_since(t0)
        
        # Save assistant opening turn
        t0 = time.perf_counter()
        turn_sequence = get_next_turn_sequence(interaction_id)
        timings["get_next_turn_sequence_opening_ms"] = _ms_since(t0)
        t0 = time.perf_counter()
        save_turn(
            interaction_id=interaction_id,
            thread_id=thread_id,
            speaker="assistant",
            text=opening_message,
            turn_sequence=turn_sequence,
            artifacts_json={"state": "intro", "signup_mode": signup_mode}
        )
        timings["save_turn_opening_ms"] = _ms_since(t0)
        
        # Generate audio and return TwiML with Gather
        try:
            t0 = time.perf_counter()
            audio_path = generate_tts(opening_message)
            timings["tts_opening_ms"] = _ms_since(t0)
        except Exception as e:
            print(f"⚠️ TTS generation failed: {e}")
            import traceback
            traceback.print_exc()
            audio_path = ""  # Fallback to text-to-speech
        
        # Build turn endpoint URL (use /voice/qualify for subsequent turns)
        # Priority: API_BASE_URL > RENDER_EXTERNAL_URL > request_url_root > default
        try:
            base_url = (
                os.getenv("API_BASE_URL") or 
                os.getenv("RENDER_EXTERNAL_URL") or 
                (request_url_root.rstrip('/') if request_url_root else '') or
                "https://execflex-backend-1.onrender.com"
            )
            if not base_url or not base_url.startswith('http'):
                base_url = (request_url_root.rstrip('/') if request_url_root else 'https://execflex-backend-1.onrender.com')
            # Use /voice/qualify for subsequent turns (same endpoint handles both initial and turns)
            turn_endpoint_url = f"{base_url}/voice/qualify?job_id={job_id}"
        except Exception as e:
            print(f"⚠️ Error building turn endpoint URL: {e}")
            # Fallback URL
            base_url = os.getenv("API_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://execflex-backend-1.onrender.com"
            turn_endpoint_url = f"{base_url}/voice/qualify?job_id={job_id}" if job_id else f"{base_url}/voice/qualify"
        
        try:
            t0 = time.perf_counter()
            twiml = _build_gather_response(resp, opening_message, audio_path, request_url_root, turn_endpoint_url)
            timings["build_gather_response_opening_ms"] = _ms_since(t0)
            timings["total_ms"] = _ms_since(total_start)
            _log_timing("voice_qualify_turn", {
                "call_sid": call_sid,
                "job_id": job_id,
                "interaction_id": interaction_id,
                "is_opening": True,
                "has_audio": bool(audio_path),
                "signup_mode": signup_mode,
                "timings_ms": timings,
            })
            return twiml, None
        except Exception as e:
            print(f"❌ Error building gather response: {e}")
            import traceback
            traceback.print_exc()
            # Return a simple response as fallback
            resp.say("Hello, this is ExecFlex. We're calling to welcome you.", voice="alice", language="en-GB")
            resp.hangup()
            return resp, None

    # No speech but NOT first turn -> Gather likely timed out, so reprompt instead of restarting.
    if not user_speech and not is_first_turn:
        t0 = time.perf_counter()
        recent_turns = get_conversation_turns(interaction_id, limit=10)
        timings["get_conversation_turns_no_input_ms"] = _ms_since(t0)

        last_assistant_text = None
        for t in reversed(recent_turns):
            if t.get("speaker") == "assistant" and t.get("text"):
                last_assistant_text = t["text"]
                break

        reprompt_text = (
            "Sorry — I didn’t catch that. "
            + (last_assistant_text if last_assistant_text else "Could you say that again?")
        )

        # Save assistant reprompt turn (useful for debugging + analytics)
        try:
            t0 = time.perf_counter()
            turn_sequence = get_next_turn_sequence(interaction_id)
            timings["get_next_turn_sequence_reprompt_ms"] = _ms_since(t0)
            t0 = time.perf_counter()
            save_turn(
                interaction_id=interaction_id,
                thread_id=thread_id,
                speaker="assistant",
                text=reprompt_text,
                turn_sequence=turn_sequence,
                artifacts_json={"state": "reprompt_no_input", "signup_mode": signup_mode}
            )
            timings["save_turn_reprompt_ms"] = _ms_since(t0)
        except Exception as e:
            print(f"⚠️ Could not save reprompt turn: {e}")

        # Generate audio and return TwiML with Gather
        try:
            t0 = time.perf_counter()
            audio_path = generate_tts(reprompt_text)
            timings["tts_reprompt_ms"] = _ms_since(t0)
        except Exception as e:
            print(f"⚠️ TTS generation failed: {e}")
            import traceback
            traceback.print_exc()
            audio_path = ""

        # Build turn endpoint URL
        base_url = os.getenv("API_BASE_URL") or (request_url_root.rstrip('/') if request_url_root else None)
        if not base_url or not base_url.startswith('http'):
            base_url = os.getenv("RENDER_EXTERNAL_URL") or "https://execflex-backend-1.onrender.com"
        if not base_url.startswith('http'):
            base_url = (request_url_root.rstrip('/') if request_url_root else '')
        turn_endpoint_url = f"{base_url}/voice/qualify?job_id={job_id}" if job_id else f"{base_url}/voice/qualify"

        t0 = time.perf_counter()
        resp = _build_gather_response(resp, reprompt_text, audio_path, request_url_root, turn_endpoint_url)
        timings["build_gather_response_reprompt_ms"] = _ms_since(t0)

        timings["total_ms"] = _ms_since(total_start)
        _log_timing("voice_qualify_turn", {
            "call_sid": call_sid,
            "job_id": job_id,
            "interaction_id": interaction_id,
            "is_opening": False,
            "no_input_reprompt": True,
            "has_audio": bool(audio_path),
            "signup_mode": signup_mode,
            "existing_role": existing_role,
            "timings_ms": timings,
        })
        return resp, None
    
    # User turn: save user speech
    t0 = time.perf_counter()
    turn_sequence = get_next_turn_sequence(interaction_id)
    timings["get_next_turn_sequence_user_ms"] = _ms_since(t0)
    t0 = time.perf_counter()
    save_turn(
        interaction_id=interaction_id,
        thread_id=thread_id,
        speaker="user",
        text=user_speech,
        turn_sequence=turn_sequence,
        raw_payload={
            "speech_confidence": speech_confidence,
            "call_sid": call_sid
        }
    )
    timings["save_turn_user_ms"] = _ms_since(t0)

    # Deterministic early-exit: user asked to end / call back / stop
    if _user_wants_to_end_call(user_speech):
        assistant_text = (
            "No problem at all. I’ll stop here. If you’d like, you can come back to ExecFlex anytime and we can pick this up later. "
            "Thanks for your time — goodbye."
        )
        extracted_updates = {}
        is_complete = True
        next_state = "complete"

        # Save assistant turn
        t0 = time.perf_counter()
        turn_sequence = get_next_turn_sequence(interaction_id)
        timings["get_next_turn_sequence_assistant_ms"] = _ms_since(t0)
        t0 = time.perf_counter()
        save_turn(
            interaction_id=interaction_id,
            thread_id=thread_id,
            speaker="assistant",
            text=assistant_text,
            turn_sequence=turn_sequence,
            artifacts_json={
                "next_state": next_state,
                "is_complete": is_complete,
                "extracted_updates": extracted_updates,
                "confidence": 1.0,
                "ended_by": "user_request"
            }
        )
        timings["save_turn_assistant_ms"] = _ms_since(t0)

        # Generate audio + hang up
        try:
            t0 = time.perf_counter()
            audio_path = generate_tts(assistant_text)
            timings["tts_assistant_ms"] = _ms_since(t0)
        except Exception as e:
            print(f"⚠️ TTS generation failed: {e}")
            import traceback
            traceback.print_exc()
            audio_path = ""

        if audio_path and request_url_root:
            base_url = os.getenv("API_BASE_URL") or request_url_root.rstrip('/') if request_url_root else None
            if not base_url or not base_url.startswith('http'):
                base_url = os.getenv("RENDER_EXTERNAL_URL") or "https://execflex-backend-1.onrender.com"
            if not base_url.startswith('http'):
                base_url = request_url_root.rstrip('/')
            full_audio_url = f"{base_url}{audio_path}"
            resp.play(full_audio_url)
        else:
            resp.say(assistant_text, voice="alice", language="en-GB")

        resp.hangup()
        timings["total_ms"] = _ms_since(total_start)
        _log_timing("voice_qualify_turn", {
            "call_sid": call_sid,
            "job_id": job_id,
            "interaction_id": interaction_id,
            "is_opening": False,
            "has_audio": bool(audio_path),
            "signup_mode": signup_mode,
            "existing_role": existing_role,
            "next_state": next_state,
            "is_complete": is_complete,
            "timings_ms": timings,
        })
        return resp, None
    
    # Get conversation history
    t0 = time.perf_counter()
    conversation_turns = get_conversation_turns(interaction_id, limit=20)
    timings["get_conversation_turns_ms"] = _ms_since(t0)
    
    # CRITICAL: Refresh existing_role from DB before generating response
    # This ensures we use the most up-to-date role (in case it was updated in a previous turn)
    if user_id:
        try:
            t0 = time.perf_counter()
            role_resp = supabase_client.table("role_assignments")\
                .select("role")\
                .eq("user_id", user_id)\
                .order("confidence", desc=True)\
                .limit(1)\
                .execute()
            timings["refresh_role_ms"] = _ms_since(t0)
            
            if role_resp.data:
                existing_role = role_resp.data[0].get("role")
                print(f"🔄 Using existing_role from DB: {existing_role}")
        except Exception as e:
            print(f"⚠️ Could not refresh role: {e}")
    
    # Generate next assistant response using OpenAI
    t0 = time.perf_counter()
    ai_response = generate_qualification_response(
        conversation_turns=conversation_turns,
        signup_mode=signup_mode,
        existing_profile=existing_profile,
        existing_role=existing_role  # Use most recent role from DB
    )
    timings["openai_generate_ms"] = _ms_since(t0)
    
    assistant_text = ai_response.get("assistant_text", "I didn't catch that. Could you repeat?")
    extracted_updates = ai_response.get("extracted_updates", {})
    is_complete = ai_response.get("is_complete", False)
    next_state = ai_response.get("next_state", "unknown")
    
    # Apply extracted updates to DB
    if extracted_updates and user_id:
        t0 = time.perf_counter()
        apply_results = apply_extracted_updates(
            user_id=user_id,
            extracted_updates=extracted_updates,
            interaction_id=interaction_id
        )
        timings["apply_extracted_updates_ms"] = _ms_since(t0)
        print(f"📝 Applied DB updates: {apply_results}")
        
        # CRITICAL: If role was just updated, log it for next turn
        # The next turn will pick it up via the refresh above
        role_updates = extracted_updates.get("role_assignments", {})
        if role_updates.get("role") and role_updates.get("role") in ("talent", "hirer"):
            print(f"🎯 Role updated in this turn: {role_updates.get('role')} - will be used in next turn")
    
    # Save assistant turn
    t0 = time.perf_counter()
    turn_sequence = get_next_turn_sequence(interaction_id)
    timings["get_next_turn_sequence_assistant_ms"] = _ms_since(t0)
    t0 = time.perf_counter()
    save_turn(
        interaction_id=interaction_id,
        thread_id=thread_id,
        speaker="assistant",
        text=assistant_text,
        turn_sequence=turn_sequence,
        artifacts_json={
            "next_state": next_state,
            "is_complete": is_complete,
            "extracted_updates": extracted_updates,
            "confidence": ai_response.get("confidence", 0.0)
        }
    )
    timings["save_turn_assistant_ms"] = _ms_since(t0)
    
    # Generate audio
    try:
        t0 = time.perf_counter()
        audio_path = generate_tts(assistant_text)
        timings["tts_assistant_ms"] = _ms_since(t0)
    except Exception as e:
        print(f"⚠️ TTS generation failed: {e}")
        import traceback
        traceback.print_exc()
        audio_path = ""  # Fallback to text-to-speech
    
    if is_complete:
        # Conversation complete - play final message and hang up
        if audio_path and request_url_root:
            base_url = os.getenv("API_BASE_URL") or request_url_root.rstrip('/') if request_url_root else None
            if not base_url or not base_url.startswith('http'):
                base_url = os.getenv("RENDER_EXTERNAL_URL") or "https://execflex-backend-1.onrender.com"
            if not base_url.startswith('http'):
                base_url = request_url_root.rstrip('/')
            full_audio_url = f"{base_url}{audio_path}"
            resp.play(full_audio_url)
        else:
            resp.say(assistant_text, voice="alice", language="en-GB")
        
        resp.hangup()
    else:
        # Continue conversation
        # Build turn endpoint URL (use /voice/qualify for subsequent turns)
        base_url = os.getenv("API_BASE_URL") or (request_url_root.rstrip('/') if request_url_root else None)
        if not base_url or not base_url.startswith('http'):
            base_url = os.getenv("RENDER_EXTERNAL_URL") or "https://execflex-backend-1.onrender.com"
        if not base_url.startswith('http'):
            base_url = (request_url_root.rstrip('/') if request_url_root else '')
        turn_endpoint_url = f"{base_url}/voice/qualify?job_id={job_id}"
        
        t0 = time.perf_counter()
        resp = _build_gather_response(resp, assistant_text, audio_path, request_url_root, turn_endpoint_url)
        timings["build_gather_response_turn_ms"] = _ms_since(t0)
    
    timings["total_ms"] = _ms_since(total_start)
    _log_timing("voice_qualify_turn", {
        "call_sid": call_sid,
        "job_id": job_id,
        "interaction_id": interaction_id,
        "is_opening": False,
        "has_audio": bool(audio_path),
        "signup_mode": signup_mode,
        "existing_role": existing_role,
        "next_state": next_state,
        "is_complete": is_complete,
        "timings_ms": timings,
    })
    return resp, None


def _build_gather_response(
    resp: VoiceResponse,
    message: str,
    audio_path: Optional[str],
    request_url_root: Optional[str],
    turn_endpoint_url: Optional[str] = None
) -> VoiceResponse:
    """
    Build a TwiML response with Gather for collecting user speech.
    
    Args:
        resp: VoiceResponse object
        message: Message text to play
        audio_path: Optional path to pre-generated audio
        request_url_root: Base URL for audio URLs
        turn_endpoint_url: Full URL to /onboarding/turn endpoint
        
    Returns:
        VoiceResponse with Gather configured
    """
    # Build turn endpoint URL if not provided
    # Note: This should not happen in normal flow since we pass job_id explicitly
    # This is a fallback for edge cases
    if not turn_endpoint_url:
        base_url = os.getenv("API_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://execflex-backend-1.onrender.com"
        if not base_url.startswith('http'):
            base_url = (request_url_root.rstrip('/') if request_url_root else 'https://execflex-backend-1.onrender.com')
        # Fallback: use /voice/qualify (but job_id should be passed explicitly)
        turn_endpoint_url = f"{base_url}/voice/qualify"
    
    if audio_path and request_url_root:
        # Use generated/cached audio
        # Priority: API_BASE_URL > RENDER_EXTERNAL_URL > request_url_root
        base_url = (
            os.getenv("API_BASE_URL") or 
            os.getenv("RENDER_EXTERNAL_URL") or 
            (request_url_root.rstrip('/') if request_url_root else None) or
            "https://execflex-backend-1.onrender.com"
        )
        if not base_url.startswith('http'):
            base_url = request_url_root.rstrip('/') if request_url_root else 'https://execflex-backend-1.onrender.com'
        full_audio_url = f"{base_url}{audio_path}"
        
        gather = Gather(
            input="speech",
            action=turn_endpoint_url,
            method="POST",
            timeout=10,
            speech_timeout="auto",
            language="en-GB",
            speech_model="phone_call"
        )
        gather.play(full_audio_url)
        resp.append(gather)
    else:
        # Fallback to text-to-speech
        gather = Gather(
            input="speech",
            action=turn_endpoint_url,
            method="POST",
            timeout=10,
            speech_timeout="auto",
            language="en-GB",
            speech_model="phone_call"
        )
        gather.say(message, voice="alice", language="en-GB")
        resp.append(gather)
    
    # Redirect if no input (redirect to same endpoint)
    resp.redirect(turn_endpoint_url)
    
    return resp

