"""
Qualification call routes for outbound call management.
"""
import os
from flask import request, Response, jsonify
from flask_cors import cross_origin
from routes import qualification_bp
from utils.response_helpers import ok, bad
from services.qualification_call_service import enqueue_qualification_call, process_queued_jobs
from config.clients import VoiceResponse, twilio_client
from config.app_config import TWILIO_PHONE_NUMBER


@qualification_bp.route("/enqueue", methods=["POST", "OPTIONS"])
@cross_origin()
def enqueue_call():
    """
    Enqueue a qualification call job (non-blocking).
    Called after user signup to trigger qualification call.
    
    This endpoint should be called from the frontend after successful signup,
    or can be triggered via Supabase webhook/database function.
    
    Body (JSON, optional): { "user_id": "uuid" }
    """
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id")
        
        # Non-blocking: enqueue and return immediately
        result = enqueue_qualification_call(user_id=user_id)
        return ok(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Don't fail signup if qualification call enqueue fails
        # Log error but return success to avoid blocking signup flow
        print(f"⚠️ Warning: Failed to enqueue qualification call: {str(e)}")
        return ok({"status": "enqueue_failed", "error": str(e)})


@qualification_bp.route("/qualification/intro", methods=["POST", "GET"])
def qualification_intro():
    """
    TwiML endpoint for qualification call opening message.
    Called by Twilio when the outbound call is answered.
    
    Query params: job_id (optional, for tracking)
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    job_id = request.values.get("job_id") or request.args.get("job_id")
    call_sid = request.values.get("CallSid") or "unknown"
    
    resp = VoiceResponse()
    
    # Try to use pre-recorded audio if available
    # Check for a qualification-specific audio file
    # Note: Reusing existing audio infrastructure from PoC (backend/static/audio/)
    # For MVP, using <Say> - can be upgraded to <Play> with audio file later
    base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", "https://api.execflex.ai"))
    audio_url = f"{base_url}/static/audio/qualification_intro.mp3"
    
    # For now, use <Say> as fallback (we can add audio file later)
    # Reusing PoC pattern: simple, clear message
    message = (
        "Hello, this is ExecFlex. We're calling to welcome you and learn more about your needs. "
        "Are you looking to hire executive talent, or are you an executive looking for opportunities?"
    )
    
    resp.say(message, voice="alice", language="en-GB")
    
    # For MVP, just say the message and hang up
    # Later: add <Gather> for response collection
    resp.hangup()
    
    return Response(str(resp), mimetype="text/xml")


@qualification_bp.route("/qualification/status", methods=["POST"])
def qualification_status():
    """
    Twilio status callback webhook.
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
        if interaction_id:
            interaction_update = {
                "status": "completed" if call_status == "completed" else "failed",
                "raw_payload": request.form.to_dict()
            }
            
            if call_status == "completed" and call_duration:
                interaction_update["ended_at"] = datetime.utcnow().isoformat()
            
            supabase_client.table("interactions")\
                .update(interaction_update)\
                .eq("id", interaction_id)\
                .execute()
        
        print(f"✅ Updated qualification call status: job_id={job_id}, call_sid={call_sid}, status={call_status}")
        return Response("OK", status=200), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error updating call status: {e}")
        # Return 200 to prevent Twilio retries
        return Response("OK", status=200), 200


@qualification_bp.route("/process-jobs", methods=["POST"])
def process_jobs_endpoint():
    """
    Endpoint to trigger job processing (for cron/scheduled tasks).
    Can be called by Render cron jobs or external schedulers.
    
    Optional query param: limit (default 10)
    """
    limit = int(request.args.get("limit", 10))
    
    try:
        processed = process_queued_jobs(limit=limit)
        return ok({"processed": processed, "message": f"Processed {processed} jobs"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return bad(f"Failed to process jobs: {str(e)}", 500)
