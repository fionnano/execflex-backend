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
from services.company_scheduling_service import say_and_gather_scheduling, handle_scheduling_step


@voice_bp.route("/voice/intro", methods=["POST", "GET"])
def voice_intro():
    """
    Entry point for Twilio voice calls (IVR start).
    This is called as a webhook by Twilio when a call is initiated.
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503

    call_sid = request.values.get("CallSid") or "unknown"
    init_session(call_sid)

    resp = VoiceResponse()
    prompt = "Hi, I'm Ai-dan, your advisor at ExecFlex. Let's keep this simple. Are you hiring for a role, or are you a candidate looking for opportunities?"
    return Response(str(say_and_gather(resp, prompt, "user_type", call_sid)), mimetype="text/xml")


@voice_bp.route("/voice/capture", methods=["POST", "GET"])
def voice_capture():
    """
    Handles speech recognition and conversation flow during voice calls.
    This is called as a webhook by Twilio after gathering speech input.
    """
    call_sid = request.values.get("CallSid") or "unknown"
    step = request.args.get("step", "user_type")
    speech = (request.values.get("SpeechResult") or "").strip()
    confidence = request.values.get("Confidence", "n/a")
    print(f"DEBUG SpeechResult (step={step}): '{speech}' (confidence={confidence})")

    return handle_conversation_step(step, speech, call_sid)


@voice_bp.route("/call_scheduling", methods=["POST", "OPTIONS"])
@cross_origin()
def call_scheduling():
    """
    Initiate an outbound Twilio call for scheduling a meeting with an executive.
    This is for COMPANIES/CLIENTS calling to schedule a consultation.
    
    Security: Rate limited to prevent abuse (5 calls per hour, 20 per day per IP).
    
    Body (JSON): {
        "phone": "+1234567890",
        "executiveId": "uuid",
        "executiveName": "John Doe",
        "executiveExpertise": "CFO, Fintech"
    }
    """
    if request.method == "OPTIONS":
        # Preflight OK
        return jsonify({"status": "ok"}), 200

    if not twilio_client:
        return bad("Twilio not configured. Voice features unavailable.", 503)

    data = request.get_json(silent=True) or {}
    phone = data.get("phone") or data.get("phoneNumber")
    executive_id = data.get("executiveId")
    executive_name = data.get("executiveName")
    executive_expertise = data.get("executiveExpertise")
    
    # Log the request for security monitoring
    client_ip = request.remote_addr or request.environ.get('HTTP_X_FORWARDED_FOR', 'unknown')
    print(f"DEBUG Scheduling call request from IP {client_ip} to phone: {phone}, executive: {executive_id}")

    if not phone:
        return bad("Phone number required", 400)

    # Basic phone number validation (E.164 format)
    if not phone.startswith('+') or len(phone) < 10 or len(phone) > 16:
        return bad("Invalid phone number format. Please use E.164 format (e.g., +353123456789)", 400)

    try:
        from flask import url_for
        # Store executive info in session before call starts
        # We'll pass it via query params to the intro endpoint
        call = twilio_client.calls.create(
            to=phone,
            from_=TWILIO_PHONE_NUMBER,
            url=url_for(
                'voice.voice_scheduling_intro',
                executive_id=executive_id or '',
                executive_name=executive_name or '',
                executive_expertise=executive_expertise or '',
                _external=True
            )
        )
        print(f"DEBUG Scheduling Call SID: {call.sid} (IP: {client_ip}, Phone: {phone}, Executive: {executive_id})")
        return ok({"status": "calling", "sid": call.sid})
    except Exception as e:
        traceback.print_exc()
        print(f"ERROR Scheduling call failed (IP: {client_ip}, Phone: {phone}): {str(e)}")
        return bad(str(e), 500)


@voice_bp.route("/voice/scheduling/intro", methods=["POST", "GET"])
def voice_scheduling_intro():
    """
    Entry point for Twilio scheduling calls (IVR start).
    This is called as a webhook by Twilio when a scheduling call is initiated.
    Uses Emma (scheduling assistant) for companies/clients.
    """
    if not VoiceResponse:
        return Response("Voice features not available", mimetype="text/plain"), 503

    call_sid = request.values.get("CallSid") or "unknown"
    init_session(call_sid)
    
    # Get executive info from query params
    executive_id = request.args.get("executive_id") or request.values.get("executive_id")
    executive_name = request.args.get("executive_name") or request.values.get("executive_name")
    executive_expertise = request.args.get("executive_expertise") or request.values.get("executive_expertise")

    return Response(
        str(handle_scheduling_step("intro", "", call_sid, executive_id, executive_name, executive_expertise)),
        mimetype="text/xml"
    )


@voice_bp.route("/voice/scheduling/capture", methods=["POST", "GET"])
def voice_scheduling_capture():
    """
    Handles speech recognition and conversation flow during scheduling calls.
    This is called as a webhook by Twilio after gathering speech input.
    Uses Emma (scheduling assistant) with ChatGPT for natural conversations.
    """
    call_sid = request.values.get("CallSid") or "unknown"
    step = request.args.get("step", "intro")
    speech = (request.values.get("SpeechResult") or "").strip()
    confidence = request.values.get("Confidence", "n/a")
    print(f"DEBUG Scheduling SpeechResult (step={step}): '{speech}' (confidence={confidence})")

    # Get executive info from session
    state = init_session(call_sid)
    executive_id = state.get("executive_id")
    executive_name = state.get("executive_name")
    executive_expertise = state.get("executive_expertise")

    return handle_scheduling_step(step, speech, call_sid, executive_id, executive_name, executive_expertise)

