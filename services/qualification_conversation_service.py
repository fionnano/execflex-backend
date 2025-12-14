"""
Qualification Conversation Service - Handles turn-based conversation flow.
Separated from provisioning/onboarding logic.
"""
from typing import Dict, Optional, List, Any, Tuple
from flask import url_for
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
            "Hello, this is ExecFlex. We're calling to welcome you and help you find executive opportunities. "
            "Let's get started with a few quick questions about what you're looking for."
        )
    elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
        return (
            "Hello, this is ExecFlex. We're calling to welcome you and help you find executive talent for your organization. "
            "Let's get started with a few quick questions about your hiring needs."
        )
    else:
        # Unknown signup_mode - ask to clarify
        return (
            "Hello, this is ExecFlex. We're calling to welcome you and learn more about your needs. "
            "Are you looking to hire executive talent, or are you an executive looking for opportunities?"
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
    
    # Get call context
    context = get_call_context(call_sid, job_id)
    if not context.get("interaction"):
        return None, f"Could not get/create interaction for call {call_sid}"
    
    interaction_id = context["interaction_id"]
    thread_id = context["thread_id"]
    user_id = context["user_id"]
    signup_mode = context["signup_mode"]
    existing_profile = context["existing_profile"]
    existing_role = context["existing_role"]
    
    resp = VoiceResponse()
    
    # If this is the opening turn (no user speech), generate opening message
    if not user_speech:
        opening_message = generate_opening_message(signup_mode)
        
        # Save assistant opening turn
        turn_sequence = get_next_turn_sequence(interaction_id)
        save_turn(
            interaction_id=interaction_id,
            thread_id=thread_id,
            speaker="assistant",
            text=opening_message,
            turn_sequence=turn_sequence,
            artifacts_json={"state": "intro", "signup_mode": signup_mode}
        )
        
        # Generate audio and return TwiML with Gather
        audio_path = generate_tts(opening_message)
        
        # Build turn endpoint URL (use unified /voice/turn)
        base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request_url_root.rstrip('/') if request_url_root else ''))
        if not base_url.startswith('http'):
            base_url = (request_url_root.rstrip('/') if request_url_root else '')
        # Use /voice/qualify for subsequent turns (same endpoint handles both initial and turns)
        turn_endpoint_url = f"{base_url}/voice/qualify?job_id={job_id}"
        
        return _build_gather_response(resp, opening_message, audio_path, request_url_root, turn_endpoint_url), None
    
    # User turn: save user speech
    turn_sequence = get_next_turn_sequence(interaction_id)
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
    
    # Get conversation history
    conversation_turns = get_conversation_turns(interaction_id, limit=20)
    
    # Generate next assistant response using OpenAI
    ai_response = generate_qualification_response(
        conversation_turns=conversation_turns,
        signup_mode=signup_mode,
        existing_profile=existing_profile,
        existing_role=existing_role
    )
    
    assistant_text = ai_response.get("assistant_text", "I didn't catch that. Could you repeat?")
    extracted_updates = ai_response.get("extracted_updates", {})
    is_complete = ai_response.get("is_complete", False)
    next_state = ai_response.get("next_state", "unknown")
    
    # Apply extracted updates to DB
    if extracted_updates and user_id:
        apply_results = apply_extracted_updates(
            user_id=user_id,
            extracted_updates=extracted_updates,
            interaction_id=interaction_id
        )
        print(f"📝 Applied DB updates: {apply_results}")
    
    # Save assistant turn
    turn_sequence = get_next_turn_sequence(interaction_id)
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
    
    # Generate audio
    audio_path = generate_tts(assistant_text)
    
    if is_complete:
        # Conversation complete - play final message and hang up
        if audio_path and request_url_root:
            base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request_url_root.rstrip('/')))
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
        base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request_url_root.rstrip('/') if request_url_root else ''))
        if not base_url.startswith('http'):
            base_url = (request_url_root.rstrip('/') if request_url_root else '')
        turn_endpoint_url = f"{base_url}/voice/qualify?job_id={job_id}"
        
        resp = _build_gather_response(resp, assistant_text, audio_path, request_url_root, turn_endpoint_url)
    
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
    if not turn_endpoint_url:
        base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request_url_root.rstrip('/') if request_url_root else ''))
        if not base_url.startswith('http'):
            base_url = (request_url_root.rstrip('/') if request_url_root else '')
        turn_endpoint_url = f"{base_url}/onboarding/turn"
    
    if audio_path and request_url_root:
        # Use generated/cached audio
        base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request_url_root.rstrip('/')))
        if not base_url.startswith('http'):
            base_url = request_url_root.rstrip('/')
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
