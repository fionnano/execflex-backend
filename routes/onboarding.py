"""
Onboarding service routes for outbound call management.
Handles onboarding calls triggered after user signup.
"""
import os
from flask import request, Response, jsonify
from routes import onboarding_bp
from utils.response_helpers import ok, bad
from utils.auth_helpers import require_admin, get_authenticated_user_id
from services.onboarding_service import initialize_user_onboarding, process_queued_jobs
from services.tts_service import get_cached_audio_path
from config.clients import VoiceResponse, twilio_client
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
    
    Query params: job_id (optional, for tracking)
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    job_id = request.values.get("job_id") or request.args.get("job_id")
    call_sid = request.values.get("CallSid") or "unknown"
    
    resp = VoiceResponse()
    
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
    
    # Tailor message based on signup_mode
    if signup_mode == "talent":
        # User signed up as talent - skip the "are you hiring or looking" question
        onboarding_message = (
            "Hello, this is ExecFlex. We're calling to welcome you and help you find executive opportunities. "
            "Let's get started with a few quick questions."
        )
    elif signup_mode == "hirer":
        # User signed up as hirer - skip the "are you hiring or looking" question
        onboarding_message = (
            "Hello, this is ExecFlex. We're calling to welcome you and help you find executive talent. "
            "Let's get started with a few quick questions."
        )
    else:
        # Unknown signup_mode - ask the question
        onboarding_message = (
            "Hello, this is ExecFlex. We're calling to welcome you and learn more about your needs. "
            "Are you looking to hire executive talent, or are you an executive looking for opportunities?"
        )
    
    # Try to find a cached audio file for the message
    cached_path = get_cached_audio_path(onboarding_message)
    
    if cached_path:
        # Use pre-cached audio file
        base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", request.url_root.rstrip('/')))
        if not base_url.startswith('http'):
            # If API_BASE_URL is not set, construct from request
            base_url = request.url_root.rstrip('/')
        full_audio_url = f"{base_url}{cached_path}"
        print(f"🎵 Using pre-cached audio: {full_audio_url}")
        resp.play(full_audio_url)
    else:
        # Fallback to text-to-speech if audio not cached
        print("⚠️ No cached audio found, using <Say> fallback")
        resp.say(onboarding_message, voice="alice", language="en-GB")
    
    # For MVP, just play the message and hang up
    # Later: add <Gather> for response collection
    resp.hangup()
    
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
