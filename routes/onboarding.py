"""
Onboarding service routes for outbound call management.
Handles onboarding calls triggered after user signup.
"""
import os
from flask import request, Response, jsonify
from routes import onboarding_bp
from utils.response_helpers import ok, bad
from utils.auth_helpers import require_admin, get_authenticated_user_id
from utils.twilio_helpers import require_twilio_signature
from services.onboarding_service import initialize_user_onboarding, process_queued_jobs
from services.tts_service import get_cached_audio_path, generate_tts
from services.qualification_turn_service import (
    get_or_create_interaction_for_call,
    get_next_turn_sequence,
    save_turn,
    get_conversation_turns,
    apply_extracted_updates
)
from services.qualification_agent_service import generate_qualification_response
from config.clients import VoiceResponse, twilio_client, Gather
from config.app_config import TWILIO_PHONE_NUMBER
from flask import url_for


@onboarding_bp.route("/enqueue", methods=["POST"])
@require_admin
def enqueue_call():
    """
    Manually trigger onboarding for a user (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Used for manual/admin triggers, testing, or re-triggering onboarding.
    
    **Note:** Automatic onboarding is handled by database trigger on signup,
    so this endpoint is primarily for admin operations or edge cases.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Body (JSON, required): { "user_id": "uuid" }
        - The user_id to initialize onboarding for (can be any user, not just the authenticated admin)
    """
    try:
        # Get authenticated admin user (for logging/audit)
        admin_user_id = request.environ.get('authenticated_user_id')
        
        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        
        if not target_user_id:
            return bad("user_id is required", 400)
        
        print(f"🔐 Admin {admin_user_id} triggering onboarding for user {target_user_id}")
        
        # Initialize onboarding (will check if already done by trigger)
        result = initialize_user_onboarding(user_id=target_user_id)
        return ok(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"⚠️ Warning: Failed to initialize onboarding: {str(e)}")
        return ok({"status": "onboarding_failed", "error": str(e)})


@onboarding_bp.route("/onboarding/intro", methods=["POST", "GET"])
def onboarding_intro():
    """
    TwiML endpoint for onboarding call opening message.
    Called by Twilio when the outbound call is answered.
    Starts the turn-based qualification conversation.
    
    Query params: job_id (optional, for tracking)
    """
    if not VoiceResponse or not Gather:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    job_id = request.values.get("job_id") or request.args.get("job_id")
    call_sid = request.values.get("CallSid") or "unknown"
    
    resp = VoiceResponse()
    
    # Get or create interaction for this call
    interaction = get_or_create_interaction_for_call(call_sid, job_id)
    if not interaction:
        print(f"⚠️ Could not get/create interaction for call {call_sid}")
        resp.say("Sorry, there was an error. Goodbye.", voice="alice", language="en-GB")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")
    
    interaction_id = interaction["id"]
    thread_id = interaction.get("thread_id")
    user_id = interaction.get("user_id")
    
    # Fetch job to get signup_mode from artifacts
    signup_mode = None
    if job_id:
        try:
            from config.clients import supabase_client
            job_resp = supabase_client.table("outbound_call_jobs")\
                .select("artifacts")\
                .eq("id", job_id)\
                .limit(1)\
                .execute()
            
            if job_resp.data and len(job_resp.data) > 0:
                artifacts = job_resp.data[0].get("artifacts", {}) or {}
                signup_mode = artifacts.get("signup_mode")
        except Exception as e:
            print(f"⚠️ Could not fetch job artifacts: {e}")
    
    # Tailor opening message based on signup_mode
    if signup_mode == "talent":
        onboarding_message = (
            "Hello, this is ExecFlex. We're calling to welcome you and help you find executive opportunities. "
            "Let's get started with a few quick questions."
        )
    elif signup_mode == "hirer":
        onboarding_message = (
            "Hello, this is ExecFlex. We're calling to welcome you and help you find executive talent. "
            "Let's get started with a few quick questions."
        )
    else:
        onboarding_message = (
            "Hello, this is ExecFlex. We're calling to welcome you and learn more about your needs. "
            "Are you looking to hire executive talent, or are you an executive looking for opportunities?"
        )
    
    # Save assistant opening turn
    turn_sequence = get_next_turn_sequence(interaction_id)
    save_turn(
        interaction_id=interaction_id,
        thread_id=thread_id,
        speaker="assistant",
        text=onboarding_message,
        turn_sequence=turn_sequence,
        artifacts_json={"state": "intro", "signup_mode": signup_mode}
    )
    
    # Generate audio for opening message
    audio_path = generate_tts(onboarding_message)
    
    if audio_path:
        # Use generated/cached audio
        base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request.url_root.rstrip('/')))
        if not base_url.startswith('http'):
            base_url = request.url_root.rstrip('/')
        full_audio_url = f"{base_url}{audio_path}"
        print(f"🎵 Using audio: {full_audio_url}")
        
        # Play audio and gather response
        gather = Gather(
            input="speech",
            action=url_for("onboarding.onboarding_turn", _external=True),
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
        print("⚠️ No audio generated, using <Say> fallback")
        gather = Gather(
            input="speech",
            action=url_for("onboarding.onboarding_turn", _external=True),
            method="POST",
            timeout=10,
            speech_timeout="auto",
            language="en-GB",
            speech_model="phone_call"
        )
        gather.say(onboarding_message, voice="alice", language="en-GB")
        resp.append(gather)
    
    # If no input, redirect to turn handler
    resp.redirect(url_for("onboarding.onboarding_turn", _external=True))
    
    return Response(str(resp), mimetype="text/xml")


@onboarding_bp.route("/onboarding/turn", methods=["POST", "GET"])
@require_twilio_signature
def onboarding_turn():
    """
    TwiML endpoint for handling each conversation turn.
    Called by Twilio after Gather collects user speech.
    
    Flow:
    1. Extract user speech from Twilio request
    2. Save user turn (append-only)
    3. Get conversation history
    4. Call OpenAI to generate next response
    5. Apply extracted DB updates
    6. Save assistant turn
    7. Generate audio and return TwiML
    """
    if not VoiceResponse or not Gather:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    call_sid = request.values.get("CallSid") or "unknown"
    user_speech = (request.values.get("SpeechResult") or "").strip()
    speech_confidence = request.values.get("Confidence", "0")
    
    print(f"🎤 Turn received - CallSid: {call_sid}, Speech: '{user_speech[:50]}...', Confidence: {speech_confidence}")
    
    resp = VoiceResponse()
    
    # Get interaction for this call
    interaction = get_or_create_interaction_for_call(call_sid)
    if not interaction:
        print(f"⚠️ Could not get interaction for call {call_sid}")
        resp.say("Sorry, there was an error. Goodbye.", voice="alice", language="en-GB")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")
    
    interaction_id = interaction["id"]
    thread_id = interaction.get("thread_id")
    user_id = interaction.get("user_id")
    
    # Get job to fetch signup_mode and existing profile data
    signup_mode = None
    existing_profile = None
    existing_role = None
    
    try:
        from config.clients import supabase_client
        
        # Get job
        job_resp = supabase_client.table("outbound_call_jobs")\
            .select("id, artifacts, user_id")\
            .eq("twilio_call_sid", call_sid)\
            .limit(1)\
            .execute()
        
        if job_resp.data:
            job = job_resp.data[0]
            artifacts = job.get("artifacts", {}) or {}
            signup_mode = artifacts.get("signup_mode")
            user_id = user_id or job.get("user_id")
        
        # Get existing profile if user_id available
        if user_id:
            profile_resp = supabase_client.table("people_profiles")\
                .select("first_name, last_name, headline")\
                .eq("user_id", user_id)\
                .limit(1)\
                .execute()
            
            if profile_resp.data:
                existing_profile = profile_resp.data[0]
            
            # Get existing role
            role_resp = supabase_client.table("role_assignments")\
                .select("role")\
                .eq("user_id", user_id)\
                .order("confidence", desc=True)\
                .limit(1)\
                .execute()
            
            if role_resp.data:
                existing_role = role_resp.data[0].get("role")
    except Exception as e:
        print(f"⚠️ Could not fetch job/profile data: {e}")
    
    # Save user turn if we have speech
    if user_speech:
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
    
    # Get conversation history for OpenAI
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
    
    # Generate audio for assistant response
    audio_path = generate_tts(assistant_text)
    
    if is_complete:
        # Conversation complete - play final message and hang up
        if audio_path:
            base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request.url_root.rstrip('/')))
            if not base_url.startswith('http'):
                base_url = request.url_root.rstrip('/')
            full_audio_url = f"{base_url}{audio_path}"
            resp.play(full_audio_url)
        else:
            resp.say(assistant_text, voice="alice", language="en-GB")
        
        resp.hangup()
    else:
        # Continue conversation - play message and gather next response
        if audio_path:
            base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request.url_root.rstrip('/')))
            if not base_url.startswith('http'):
                base_url = request.url_root.rstrip('/')
            full_audio_url = f"{base_url}{audio_path}"
            
            gather = Gather(
                input="speech",
                action=url_for("onboarding.onboarding_turn", _external=True),
                method="POST",
                timeout=10,
                speech_timeout="auto",
                language="en-GB",
                speech_model="phone_call"
            )
            gather.play(full_audio_url)
            resp.append(gather)
        else:
            gather = Gather(
                input="speech",
                action=url_for("onboarding.onboarding_turn", _external=True),
                method="POST",
                timeout=10,
                speech_timeout="auto",
                language="en-GB",
                speech_model="phone_call"
            )
            gather.say(assistant_text, voice="alice", language="en-GB")
            resp.append(gather)
        
        # Redirect if no input
        resp.redirect(url_for("onboarding.onboarding_turn", _external=True))
    
    return Response(str(resp), mimetype="text/xml")


@onboarding_bp.route("/onboarding/status", methods=["POST"])
def onboarding_status():
    """
    Twilio status callback webhook for onboarding calls.
    Updates job and interaction records with call status.
    """
    call_sid = request.form.get("CallSid")
    call_status = request.form.get("CallStatus")  # queued, ringing, in-progress, completed, failed, busy, no-answer, canceled
    call_duration = request.form.get("CallDuration")  # seconds, only for completed
    from_number = request.form.get("From")
    to_number = request.form.get("To")
    
    if not call_sid:
        return Response("Missing CallSid", status=400), 400
    
    try:
        from config.clients import supabase_client
        from datetime import datetime
        
        if not supabase_client:
            print("⚠️ Supabase client not available for status update")
            return Response("OK", status=200), 200
        
        # Find job by call_sid
        job_resp = supabase_client.table("outbound_call_jobs")\
            .select("*")\
            .eq("twilio_call_sid", call_sid)\
            .limit(1)\
            .execute()
        
        if not job_resp.data:
            print(f"⚠️ No job found for call_sid: {call_sid}")
            return Response("OK", status=200), 200
        
        job = job_resp.data[0]
        job_id = job["id"]
        interaction_id = job.get("interaction_id")
        
        # Map Twilio status to job status
        status_map = {
            "completed": "succeeded",
            "failed": "failed",
            "busy": "failed",
            "no-answer": "failed",
            "canceled": "failed"
        }
        job_status = status_map.get(call_status, job.get("status", "running"))
        
        # Update job
        from datetime import timezone
        now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        update_data = {
            "status": job_status,
            "updated_at": now_iso,
            "artifacts": {
                **job.get("artifacts", {}),
                "call_status": call_status,
                "call_duration": call_duration,
                "from_number": from_number,
                "to_number": to_number,
                "status_updated_at": now_iso
            }
        }
        
        supabase_client.table("outbound_call_jobs")\
            .update(update_data)\
            .eq("id", job_id)\
            .execute()
        
        # Update interaction
        # Note: interactions table doesn't have 'status' - use ended_at to indicate completion
        if interaction_id:
            interaction_update = {
                "raw_payload": request.form.to_dict()
            }
            
            # Set ended_at if call completed or failed
            if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
                interaction_update["ended_at"] = datetime.utcnow().isoformat()
            
            # Note: interactions are append-only, so we can't update them
            # Instead, we'll store the status in the job's artifacts
            # The interaction's ended_at will be set when the call completes
            # For now, we'll just update the raw_payload via a direct SQL call if needed
            # But since interactions are append-only, we should create a new interaction record
            # For MVP, we'll just update the job and leave interaction as-is
            print(f"ℹ️  Interaction {interaction_id} status: {call_status} (interactions are append-only)")
        
        print(f"✅ Updated onboarding call status: job_id={job_id}, call_sid={call_sid}, status={call_status}")
        return Response("OK", status=200), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error updating call status: {e}")
        # Return 200 to prevent Twilio retries
        return Response("OK", status=200), 200


@onboarding_bp.route("/process-jobs", methods=["POST"])
def process_jobs_endpoint():
    """
    Endpoint to trigger job processing (for HTTP-based cron/scheduled tasks).
    
    **Note:** This endpoint is optional. If using Render Background Workers,
    you can run `python -m workers.call_dispatcher` directly instead.
    
    Optional query param: limit (default 10)
    """
    # Optional: Add service secret protection here if needed
    limit = int(request.args.get("limit", 10))
    
    try:
        processed = process_queued_jobs(limit=limit)
        return ok({"processed": processed, "message": f"Processed {processed} jobs"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return bad(f"Failed to process jobs: {str(e)}", 500)
