"""
WebSocket route handler for Twilio Media Streams.
This module is initialized from server.py with the Flask-Sock instance.
"""
import json
import asyncio
import base64
import threading
from typing import Optional
from flask_sock import Sock
from simple_websocket import Server as SimpleWebSocket

from services.realtime_session_state import get_session_manager, CallPhase
from services.voice_metrics import get_metrics_service
from config.app_config import OPENAI_API_KEY, ELEVEN_API_KEY, ELEVEN_VOICE_ID

# Import the bridge components
from services.realtime_voice_bridge import (
    BridgeConfig,
    mulaw_to_pcm16,
    pcm16_to_mulaw,
    resample_8k_to_24k,
    resample_24k_to_8k
)


def init_voice_websocket(sock: Sock):
    """Initialize the WebSocket routes with the Flask-Sock instance."""
    print("Initializing voice WebSocket routes")

    @sock.route("/voice/ws")
    def handle_voice_websocket(ws: SimpleWebSocket):
        """
        Handle Twilio Media Streams WebSocket connection.

        This endpoint receives audio from Twilio, processes it through OpenAI Realtime API,
        and sends TTS audio back to Twilio.
        """
        import sys
        print("=" * 50, file=sys.stderr)
        print("WEBSOCKET HANDLER ENTERED", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        print("WebSocket connection opened for voice streaming")

        # State for this connection
        call_sid: Optional[str] = None
        job_id: Optional[str] = None
        stream_sid: Optional[str] = None
        interaction_id: Optional[str] = None
        signup_mode: Optional[str] = None
        openai_ws = None
        session_manager = get_session_manager()
        metrics_service = get_metrics_service()

        # Audio buffer for collecting frames
        audio_buffer = bytearray()

        try:
            while True:
                # Receive message from Twilio
                message = ws.receive()
                if message is None:
                    break

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("event")

                if event_type == "connected":
                    print(f"Twilio Media Stream connected: {data.get('protocol')}")

                elif event_type == "start":
                    # Extract stream metadata
                    start_data = data.get("start", {})
                    stream_sid = start_data.get("streamSid")
                    call_sid = start_data.get("callSid")
                    custom_params = start_data.get("customParameters", {})
                    job_id = custom_params.get("job_id")

                    print(f"Stream started: stream_sid={stream_sid}, call_sid={call_sid}, job_id={job_id}")

                    # Get call context from database
                    if job_id:
                        try:
                            from config.clients import supabase_client
                            job_resp = supabase_client.table("outbound_call_jobs")\
                                .select("*")\
                                .eq("id", job_id)\
                                .limit(1)\
                                .execute()

                            if job_resp.data:
                                job = job_resp.data[0]
                                interaction_id = job.get("interaction_id")
                                artifacts = job.get("artifacts", {}) or {}
                                signup_mode = artifacts.get("signup_mode")

                                # Create session
                                session = session_manager.create_session(
                                    call_sid,
                                    job_id=job_id,
                                    interaction_id=interaction_id,
                                    user_id=job.get("user_id"),
                                    signup_mode=signup_mode
                                )
                                session.phase = CallPhase.GREETING

                                # Start metrics tracking
                                metrics_service.start_call(
                                    call_sid,
                                    job_id=job_id,
                                    interaction_id=interaction_id
                                )
                        except Exception as e:
                            print(f"Error getting job context: {e}")

                    # Connect to OpenAI Realtime API
                    try:
                        openai_ws = _connect_openai_sync(signup_mode)
                        if openai_ws:
                            # Start background thread to handle OpenAI responses
                            threading.Thread(
                                target=_handle_openai_responses,
                                args=(openai_ws, ws, stream_sid, call_sid, metrics_service),
                                daemon=True
                            ).start()

                            # Send initial greeting request
                            _send_greeting_request(openai_ws, signup_mode)
                    except Exception as e:
                        print(f"Error connecting to OpenAI: {e}")
                        # Fallback: send a simple greeting using TTS
                        _send_fallback_greeting(ws, stream_sid, signup_mode)

                elif event_type == "media":
                    # Process incoming audio from Twilio
                    media_data = data.get("media", {})
                    payload = media_data.get("payload")

                    if payload and openai_ws:
                        try:
                            # Decode mulaw audio from Twilio
                            mulaw_audio = base64.b64decode(payload)

                            # Convert to PCM16 and resample for OpenAI
                            pcm_8k = mulaw_to_pcm16(mulaw_audio)
                            pcm_24k = resample_8k_to_24k(pcm_8k)

                            # Send to OpenAI
                            audio_event = {
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(pcm_24k).decode("utf-8")
                            }
                            openai_ws.send(json.dumps(audio_event))
                        except Exception as e:
                            print(f"Error forwarding audio to OpenAI: {e}")

                elif event_type == "stop":
                    print(f"Stream stopped: stream_sid={stream_sid}")
                    break

        except Exception as e:
            print(f"WebSocket error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Clean up
            if openai_ws:
                try:
                    openai_ws.close()
                except Exception:
                    pass

            if call_sid:
                session_manager.end_session(call_sid)
                metrics_service.end_call(call_sid)

            print(f"WebSocket connection closed for call_sid={call_sid}")


def _connect_openai_sync(signup_mode: Optional[str]):
    """Connect to OpenAI Realtime API (synchronous wrapper)."""
    import websocket
    import ssl

    if not OPENAI_API_KEY:
        print("OpenAI API key not configured")
        return None

    # Use the correct model name for OpenAI Realtime API
    url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1"
    }

    print(f"Connecting to OpenAI Realtime API...")
    try:
        ws = websocket.create_connection(
            url,
            header=[f"{k}: {v}" for k, v in headers.items()],
            sslopt={"cert_reqs": ssl.CERT_REQUIRED},
            timeout=30
        )
        print("OpenAI WebSocket connected successfully")

        # Configure the session
        system_prompt = _get_system_prompt(signup_mode)
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": system_prompt,
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500
                },
                "temperature": 0.8,
                "max_response_output_tokens": 200
            }
        }
        ws.send(json.dumps(session_config))
        print("OpenAI Realtime session configured")
        return ws
    except Exception as e:
        print(f"Failed to connect to OpenAI Realtime: {e}")
        return None


def _get_system_prompt(signup_mode: Optional[str]) -> str:
    """Get the system prompt for the qualification call."""
    if signup_mode in ("talent", "job_seeker", "executive", "candidate"):
        mode_context = "The user is an executive looking for job opportunities."
    elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
        mode_context = "The user is looking to hire executive talent for their organization."
    else:
        mode_context = "Determine whether the user is looking to hire executives or is an executive seeking opportunities."

    return f"""You are Ai-dan, a friendly voice assistant for ExecFlex, a platform connecting companies with executive talent.

{mode_context}

CONVERSATION STYLE:
- Be warm, professional, and concise
- Ask ONE question at a time
- Keep responses under 20 seconds when spoken (about 50-70 words max)
- Listen actively and acknowledge what the user says
- Don't repeat questions that have been answered

CONVERSATION GOALS:
1. Confirm their intent (hiring vs job seeking)
2. Understand their motivation (why ExecFlex, why now)
3. Learn about role preferences (titles, industries)
4. Understand location and availability preferences
5. Identify any constraints or deal-breakers

IMPORTANT RULES:
- Never ask for information already provided
- If the user wants to end the call, thank them politely and close
- After 8-10 minutes or when enough info is gathered, begin closing the conversation
- Be natural and conversational, not robotic
"""


def _send_greeting_request(openai_ws, signup_mode: Optional[str]):
    """Send initial greeting request to OpenAI."""
    if signup_mode in ("talent", "job_seeker", "executive", "candidate"):
        greeting = (
            "Hi, this is Ai-dan from ExecFlex. I noticed you just logged in "
            "looking for executive opportunities. Have I caught you at a bad time?"
        )
    elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
        greeting = (
            "Hello, this is Ai-dan from ExecFlex. I noticed you just logged in "
            "looking for executive talent for your organization. Have I caught you at a bad time?"
        )
    else:
        greeting = (
            "Hello, this is Ai-dan from ExecFlex. I noticed you just logged in. "
            "Are you looking to hire executive talent, or are you an executive "
            "looking for opportunities?"
        )

    # First, add the greeting as a conversation item
    conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": f"Please greet the caller by saying: {greeting}"
                }
            ]
        }
    }
    print(f"Sending conversation item to OpenAI: {greeting[:50]}...")
    openai_ws.send(json.dumps(conversation_item))

    # Then request a response
    create_response = {
        "type": "response.create"
    }
    openai_ws.send(json.dumps(create_response))
    print("Response request sent to OpenAI")


def _handle_openai_responses(openai_ws, twilio_ws, stream_sid: str, call_sid: str, metrics_service):
    """Handle responses from OpenAI in a background thread."""
    print(f"OpenAI response handler started for call {call_sid}")
    first_audio_recorded = False
    message_count = 0

    try:
        while True:
            try:
                message = openai_ws.recv()
                if not message:
                    print(f"OpenAI WebSocket returned empty message, exiting handler")
                    break

                message_count += 1
                data = json.loads(message)
                event_type = data.get("type")

                # Log all event types for debugging (first 20 messages)
                if message_count <= 20:
                    print(f"OpenAI event #{message_count}: {event_type}")
                    # Log full data for key events
                    if event_type in ("error", "response.done", "session.updated"):
                        print(f"  Full data: {json.dumps(data)[:500]}")

                if event_type == "response.audio.delta":
                    # Streaming audio from OpenAI
                    audio_b64 = data.get("delta", "")
                    if audio_b64:
                        # Record first audio timing
                        if not first_audio_recorded:
                            metrics_service.record_first_audio(call_sid)
                            first_audio_recorded = True

                        # Convert and send to Twilio
                        try:
                            pcm_24k = base64.b64decode(audio_b64)
                            pcm_8k = resample_24k_to_8k(pcm_24k)
                            mulaw_audio = pcm16_to_mulaw(pcm_8k)

                            media_event = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": base64.b64encode(mulaw_audio).decode("utf-8")
                                }
                            }
                            twilio_ws.send(json.dumps(media_event))
                        except Exception as e:
                            print(f"Error sending audio to Twilio: {e}")

                elif event_type == "response.audio.done":
                    # Response complete
                    metrics_service.record_response_complete(call_sid)
                    first_audio_recorded = False  # Reset for next turn

                elif event_type == "input_audio_buffer.speech_stopped":
                    # User stopped speaking - record timing
                    metrics_service.record_user_speech_end(call_sid)

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    # Got transcript of user speech
                    transcript = data.get("transcript", "")
                    print(f"User said: {transcript}")

                elif event_type == "error":
                    error = data.get("error", {})
                    print(f"OpenAI error: {error}")
                    metrics_service.record_event(
                        call_sid,
                        "openai_error",
                        status="error",
                        provider="openai",
                        metadata=error
                    )

            except Exception as e:
                import traceback
                print(f"Error handling OpenAI message: {e}")
                traceback.print_exc()
                break

    except Exception as e:
        import traceback
        print(f"OpenAI response handler error: {e}")
        traceback.print_exc()
    finally:
        print(f"OpenAI response handler exiting for call {call_sid}, processed {message_count} messages")


def _send_fallback_greeting(twilio_ws, stream_sid: str, signup_mode: Optional[str]):
    """Send a fallback greeting using pre-generated TTS (when OpenAI fails)."""
    # For fallback, we'll mark the stream to send clear event and let the call fall back to /voice/qualify
    try:
        # Send mark to indicate we should end the stream
        mark_event = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": "fallback"}
        }
        twilio_ws.send(json.dumps(mark_event))
    except Exception as e:
        print(f"Error sending fallback mark: {e}")
