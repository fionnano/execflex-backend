"""
Voice/telephony routes for Ai-dan.
"""
import traceback
from flask import request, Response, jsonify
from flask_cors import cross_origin
from routes import voice_bp
from utils.response_helpers import ok, bad
from utils.rate_limiting import get_limiter
from config.clients import twilio_client, VoiceResponse
from config.app_config import TWILIO_PHONE_NUMBER
from services.voice_session_service import init_session
from services.voice_conversation_service import say_and_gather, handle_conversation_step
from services.qualification_conversation_service import handle_conversation_turn
from utils.twilio_helpers import require_twilio_signature


@voice_bp.route("/intro", methods=["POST", "GET"])
def voice_intro():
    """
    Unified entry point for Twilio voice calls.
    Called by Twilio when a call is answered.
    
    Routes to appropriate conversation handler based on call_type:
    - onboarding: Qualification conversation for new signups
    - inbound: Inbound calls from users (legacy/fallback)
    
    Query params:
        job_id: Optional job ID for outbound calls
        call_type: 'onboarding' | 'inbound' (defaults to 'inbound' for backward compatibility)
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503

    call_sid = request.values.get("CallSid") or "unknown"
    job_id = request.values.get("job_id") or request.args.get("job_id")
    call_type = request.values.get("call_type") or request.args.get("call_type", "inbound")
    
    # Route based on call type
    if call_type == "onboarding":
        # Qualification conversation for onboarding calls
        # Delegate to conversation service (opening turn, no user speech)
        resp, error = handle_conversation_turn(
            call_sid=call_sid,
            user_speech=None,  # Opening turn
            job_id=job_id,
            request_url_root=request.url_root
        )
        
        if error:
            print(f"⚠️ Error in onboarding intro: {error}")
            resp = VoiceResponse()
            resp.say("Sorry, there was an error. Goodbye.", voice="alice", language="en-GB")
            resp.hangup()
        
        return Response(str(resp), mimetype="text/xml")
    
    else:
        # Legacy inbound call flow (for backward compatibility)
        init_session(call_sid)
        resp = VoiceResponse()
        prompt = "Hi, I'm Ai-dan, your advisor at ExecFlex. Let's keep this simple. Are you hiring for a role, or are you a candidate looking for opportunities?"
        return Response(str(say_and_gather(resp, prompt, "user_type", call_sid)), mimetype="text/xml")


@voice_bp.route("/qualify", methods=["POST", "GET"])
def voice_qualify():
    """
    Outbound qualification call endpoint.
    Called by Twilio when an outbound qualification call is answered.
    
    This endpoint handles all logic for qualification calls:
    - OpenAI reasoning (with job seeker or talent seeker system prompts)
    - Storing interaction + turns in database
    - Converting text to speech with ElevenLabs
    - Incremental DB updates (people_profiles, organizations, role_assignments)
    
    Flow:
    1. Initial call: Twilio calls this endpoint when user answers → Returns opening message + Gather
    2. Subsequent turns: Twilio calls this endpoint after Gather → Processes user speech, calls OpenAI, returns next message
    
    Query params:
        job_id: Job ID from outbound_call_jobs table (required)
    
    Note: Signature verification is handled inside to allow better error handling.
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    # Check signature but don't block if in dev mode (for easier debugging)
    from utils.twilio_helpers import verify_twilio_signature
    import os
    app_env = os.getenv("APP_ENV", "prod").lower()
    
    # Reconstruct the EXACT URL that Twilio was configured with (same logic as onboarding_service.py)
    # This must match the URL used when creating the call
    base_url = (
        os.getenv("API_BASE_URL") or 
        os.getenv("RENDER_EXTERNAL_URL") or 
        "https://execflex-backend-1.onrender.com"
    )
    job_id_param = request.values.get("job_id") or request.args.get("job_id")
    if job_id_param:
        configured_url = f"{base_url.rstrip('/')}/voice/qualify?job_id={job_id_param}"
    else:
        configured_url = f"{base_url.rstrip('/')}/voice/qualify"
    
    # Use the configured URL for verification (must match what Twilio used to generate signature)
    if not verify_twilio_signature(url=configured_url):
        if app_env != "dev":
            print("❌ Invalid Twilio signature in production mode")
            # Log both URLs for debugging
            print(f"   Configured URL (what Twilio used): {configured_url}")
            print(f"   Request URL (what Flask received): {request.url}")
            return Response("Invalid signature", status=403), 403
        else:
            print("⚠️ Twilio signature verification failed, but continuing (dev mode)")
    
    call_sid = request.values.get("CallSid") or request.args.get("CallSid") or "unknown"
    job_id = job_id_param
    user_speech = (request.values.get("SpeechResult") or "").strip()
    speech_confidence = request.values.get("Confidence", "0")
    
    print(f"📞 Qualification call received: call_sid={call_sid}, job_id={job_id}, has_speech={bool(user_speech)}")
    print(f"📞 Request method: {request.method}, URL: {request.url}")
    print(f"📞 Request args: {dict(request.args)}")
    print(f"📞 Request values: {dict(request.values)}")
    
    if not job_id:
        print(f"⚠️ Missing job_id in qualification call: call_sid={call_sid}")
        resp = VoiceResponse()
        resp.say("Sorry, there was an error. Goodbye.", voice="alice", language="en-GB")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")
    
    try:
        # Handle conversation turn (works for both initial call and subsequent turns)
        # If user_speech is empty, it's the opening turn
        resp, error = handle_conversation_turn(
            call_sid=call_sid,
            user_speech=user_speech if user_speech else None,
            speech_confidence=speech_confidence,
            job_id=job_id,
            request_url_root=request.url_root
        )
        
        if error:
            print(f"⚠️ Error in qualification call: {error}")
            resp = VoiceResponse()
            resp.say("Sorry, there was an error. Goodbye.", voice="alice", language="en-GB")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")
        
        if not resp:
            print(f"❌ handle_conversation_turn returned None response (error was also None)")
            resp = VoiceResponse()
            resp.say("Sorry, there was an error. Goodbye.", voice="alice", language="en-GB")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")
        
        # Ensure we have valid TwiML
        twiml_str = str(resp)
        if not twiml_str or len(twiml_str.strip()) == 0:
            print(f"❌ Empty TwiML response generated")
            resp = VoiceResponse()
            resp.say("Sorry, there was an error. Goodbye.", voice="alice", language="en-GB")
            resp.hangup()
            return Response(str(resp), mimetype="text/xml")
        
        return Response(twiml_str, mimetype="text/xml")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Exception in voice_qualify: {e}")
        resp = VoiceResponse()
        resp.say("Sorry, there was an error. Goodbye.", voice="alice", language="en-GB")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")


@voice_bp.route("/inbound", methods=["POST", "GET"])
def voice_inbound():
    """
    Turn handler for inbound voice conversations.
    Called by Twilio after Gather collects user speech during inbound calls.
    
    This endpoint is for inbound calls only. For outbound qualification calls,
    see /voice/qualify.
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    call_sid = request.values.get("CallSid") or "unknown"
    user_speech = (request.values.get("SpeechResult") or "").strip()
    speech_confidence = request.values.get("Confidence", "0")
    step = request.args.get("step", "user_type")
    
    print(f"DEBUG SpeechResult (step={step}): '{user_speech}' (confidence={speech_confidence})")
    return handle_conversation_step(step, user_speech, call_sid)


@voice_bp.route("/capture", methods=["POST", "GET"])
def voice_capture():
    """
    Legacy endpoint for inbound call conversation flow.
    Kept for backward compatibility.
    """
    call_sid = request.values.get("CallSid") or "unknown"
    step = request.args.get("step", "user_type")
    speech = (request.values.get("SpeechResult") or "").strip()
    confidence = request.values.get("Confidence", "n/a")
    print(f"DEBUG SpeechResult (step={step}): '{speech}' (confidence={confidence})")

    return handle_conversation_step(step, speech, call_sid)


@voice_bp.route("/status", methods=["POST", "GET"])
def voice_status():
    """
    Unified status callback webhook for all Twilio voice calls.
    Updates job and interaction records with call status.
    """
    # Check signature but don't block if in dev mode (for easier debugging)
    from utils.twilio_helpers import verify_twilio_signature
    import os
    app_env = os.getenv("APP_ENV", "prod").lower()
    
    # Reconstruct the EXACT URL that Twilio was configured with (same logic as onboarding_service.py)
    # This must match the URL used in status_callback when creating the call
    base_url = (
        os.getenv("API_BASE_URL") or 
        os.getenv("RENDER_EXTERNAL_URL") or 
        "https://execflex-backend-1.onrender.com"
    )
    configured_url = f"{base_url.rstrip('/')}/voice/status"
    
    # Use the configured URL for verification (must match what Twilio used to generate signature)
    if not verify_twilio_signature(url=configured_url):
        if app_env != "dev":
            print("❌ Invalid Twilio signature in production mode")
            # Log both URLs for debugging
            print(f"   Configured URL (what Twilio used): {configured_url}")
            print(f"   Request URL (what Flask received): {request.url}")
            return Response("Invalid signature", status=403), 403
        else:
            print("⚠️ Twilio signature verification failed, but continuing (dev mode)")
    
    call_sid = request.form.get("CallSid") or request.values.get("CallSid")
    call_status = request.form.get("CallStatus") or request.values.get("CallStatus")  # queued, ringing, in-progress, completed, failed, busy, no-answer, canceled
    call_duration = request.form.get("CallDuration") or request.values.get("CallDuration")  # seconds, only for completed
    from_number = request.form.get("From") or request.values.get("From")
    to_number = request.form.get("To") or request.values.get("To")
    
    print(f"📞 Status callback: call_sid={call_sid}, status={call_status}")
    
    if not call_sid:
        print("⚠️ Missing CallSid in status callback")
        return Response("Missing CallSid", status=400), 400
    
    try:
        from config.clients import supabase_client
        from datetime import datetime, timezone
        
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
        
        # Note: interactions are append-only, so we don't update them
        # Status is stored in job artifacts
        if interaction_id:
            print(f"ℹ️  Interaction {interaction_id} status: {call_status} (interactions are append-only)")
        
        print(f"✅ Updated call status: job_id={job_id}, call_sid={call_sid}, status={call_status}")
        return Response("OK", status=200), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error updating call status: {e}")
        # Return 200 to prevent Twilio retries
        return Response("OK", status=200), 200



