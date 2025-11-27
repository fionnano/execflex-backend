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


@voice_bp.route("/call_candidate", methods=["POST", "OPTIONS"])
@cross_origin()
def call_candidate():
    """
    Initiate an outbound Twilio call to a candidate/client.
    
    Security: Rate limited to prevent abuse (5 calls per hour, 20 per day per IP).
    Rate limiting is applied in server.py after blueprint registration.
    
    Body (JSON): { "phone": "+1234567890" }
    """
    if request.method == "OPTIONS":
        # Preflight OK
        return jsonify({"status": "ok"}), 200

    if not twilio_client:
        return bad("Twilio not configured. Voice features unavailable.", 503)

    data = request.get_json(silent=True) or {}
    phone = data.get("phone")
    
    # Log the request for security monitoring
    client_ip = request.remote_addr or request.environ.get('HTTP_X_FORWARDED_FOR', 'unknown')
    print(f"DEBUG Call request from IP {client_ip} to phone: {phone}")

    if not phone:
        return bad("Phone number required", 400)

    # Basic phone number validation (E.164 format)
    if not phone.startswith('+') or len(phone) < 10 or len(phone) > 16:
        return bad("Invalid phone number format. Please use E.164 format (e.g., +353123456789)", 400)

    try:
        from flask import url_for
        call = twilio_client.calls.create(
            to=phone,
            from_=TWILIO_PHONE_NUMBER,
            url=url_for('voice.voice_intro', _external=True)
        )
        print(f"DEBUG Call SID: {call.sid} (IP: {client_ip}, Phone: {phone})")
        return ok({"status": "calling", "sid": call.sid})
    except Exception as e:
        traceback.print_exc()
        print(f"ERROR Call failed (IP: {client_ip}, Phone: {phone}): {str(e)}")
        return bad(str(e), 500)


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

