"""
Voice/telephony routes for Ai-dan (outbound qualification calls).

This module handles Twilio webhook endpoints for voice conversations:
- /voice/qualify: Main endpoint for outbound qualification calls (handles entire conversation)
- /voice/inbound: Turn handler for future inbound calls
- /voice/status: Status callback webhook for all calls

Architecture:
- Outbound calls: Worker creates call → Twilio calls /voice/qualify → Conversation service handles turns
- Status updates: Twilio automatically calls /voice/status on status changes
- All conversation logic is in services/qualification_conversation_service.py
"""
import traceback
from flask import request, Response
from routes import voice_bp
from config.clients import VoiceResponse
from services.qualification_conversation_service import handle_conversation_turn


@voice_bp.route("/qualify", methods=["POST", "GET"])
def voice_qualify():
    """
    Outbound qualification call endpoint - PRIMARY ENDPOINT FOR QUALIFICATION CALLS.
    
    This is the main entry point for all outbound qualification conversations.
    Called by Twilio when:
    1. Initial call: User answers the phone → Returns opening message + Gather
    2. Subsequent turns: After Gather collects user speech → Processes response, calls OpenAI, returns next message
    
    Conversation Flow:
    - Opening turn: No SpeechResult → generate_opening_message() → TTS → TwiML with Gather
    - User turn: SpeechResult present → save user turn → get conversation history → OpenAI → extract data → save assistant turn → TTS → TwiML
    - Completion: When is_complete=True → play final message → hangup
    
    All conversation logic is delegated to:
    - services/qualification_conversation_service.py: Turn handling, opening messages, closing
    - services/qualification_agent_service.py: OpenAI prompts, question sequences, data extraction
    - services/qualification_turn_service.py: Database operations (saving turns, applying updates)
    
    Query params:
        job_id: Job ID from outbound_call_jobs table (required) - links call to user/signup
        SpeechResult: User's spoken response (for subsequent turns, empty on initial call)
        CallSid: Twilio call identifier (automatically provided)
        Confidence: Speech recognition confidence score
    
    Security:
    - Twilio signature verification (bypasses in dev mode)
    - Returns 403 if signature invalid in production
    
    Returns:
        TwiML Response (text/xml) with either:
        - Opening message + Gather (initial call)
        - Next question + Gather (subsequent turns)
        - Final message + Hangup (conversation complete)
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    # Verify Twilio signature (RequestValidator handles request.url automatically)
    from utils.twilio_helpers import verify_twilio_signature
    import os
    app_env = os.getenv("APP_ENV", "prod").lower()
    
    if not verify_twilio_signature():
        if app_env != "dev":
            print("❌ Invalid Twilio signature in production mode")
            return Response("Invalid signature", status=403), 403
        else:
            print("⚠️ Twilio signature verification failed, but continuing (dev mode)")
    
    call_sid = request.values.get("CallSid") or request.args.get("CallSid") or "unknown"
    job_id = request.values.get("job_id") or request.args.get("job_id")
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
    Turn handler for inbound voice conversations (future use).
    
    Called by Twilio after Gather collects user speech during inbound calls.
    This endpoint is reserved for future inbound call functionality.
    
    For outbound qualification calls, see /voice/qualify.
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503
    
    # TODO: Implement inbound call handling when needed
    resp = VoiceResponse()
    resp.say("Inbound calls are not yet implemented. Please use the web interface.", voice="alice", language="en-GB")
    resp.hangup()
    return Response(str(resp), mimetype="text/xml")


@voice_bp.route("/status", methods=["POST", "GET"])
def voice_status():
    """
    Status callback webhook for all Twilio voice calls.
    
    Called automatically by Twilio when call status changes:
    - initiated, ringing, answered, in-progress, completed, failed, busy, no-answer, canceled
    
    Updates:
    - outbound_call_jobs table: Sets job status and stores call metadata (duration, numbers, etc.)
    - Note: interactions table is append-only, so status is stored in job artifacts
    
    Status Mapping:
    - completed → succeeded
    - failed, busy, no-answer, canceled → failed
    - Other statuses → keep existing job status
    
    Security:
    - Twilio signature verification (bypasses in dev mode)
    - Returns 200 OK even on errors (to prevent Twilio retries)
    
    Query params (from Twilio):
        CallSid: Twilio call identifier
        CallStatus: Current call status
        CallDuration: Call duration in seconds (only for completed calls)
        From: Caller phone number
        To: Called phone number
    """
    # Verify Twilio signature (RequestValidator handles request.url automatically)
    from utils.twilio_helpers import verify_twilio_signature
    import os
    app_env = os.getenv("APP_ENV", "prod").lower()
    
    if not verify_twilio_signature():
        if app_env != "dev":
            print("❌ Invalid Twilio signature in production mode")
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



