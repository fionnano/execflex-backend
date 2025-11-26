"""
Voice/telephony routes for Ai-dan.
"""
import traceback
from flask import request, Response, jsonify
from flask_cors import cross_origin
from routes import voice_bp
from utils.response_helpers import ok, bad
from config.clients import twilio_client, VoiceResponse
from config.app_config import TWILIO_PHONE
from services.voice_session_service import init_session
from services.voice_conversation_service import say_and_gather, handle_conversation_step


@voice_bp.route("/call_candidate", methods=["POST", "OPTIONS"])
@cross_origin()
def call_candidate():
    """
    Initiate an outbound Twilio call to a candidate/client.
    Body (JSON): { "phone": "+1234567890" }
    """
    if request.method == "OPTIONS":
        # Preflight OK
        return jsonify({"status": "ok"}), 200

    if not twilio_client:
        return bad("Twilio not configured. Voice features unavailable.", 503)

    data = request.get_json(silent=True) or {}
    phone = data.get("phone")
    print("DEBUG Incoming phone:", phone)

    if not phone:
        return bad("Phone number required", 400)

    try:
        from flask import url_for
        call = twilio_client.calls.create(
            to=phone,
            from_=TWILIO_PHONE,
            url=url_for('voice.voice_intro', _external=True)
        )
        print("DEBUG Call SID:", call.sid)
        return ok({"status": "calling", "sid": call.sid})
    except Exception as e:
        traceback.print_exc()
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

