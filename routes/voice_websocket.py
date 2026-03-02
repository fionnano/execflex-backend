"""
WebSocket route handler for Twilio Media Streams.
This module is initialized from server.py with the Flask-Sock instance.
"""
import json
import base64
import threading
import time
import struct
from typing import Optional
from flask_sock import Sock
from simple_websocket import Server as SimpleWebSocket

from services.realtime_session_state import get_session_manager, CallPhase
from services.voice_metrics import get_metrics_service
from config.app_config import OPENAI_API_KEY

# Import the bridge components
from services.realtime_voice_bridge import (
    mulaw_to_pcm16,
)


def init_voice_websocket(sock: Sock):
    """Initialize the WebSocket routes with the Flask-Sock instance."""
    print("Initializing voice WebSocket routes")

    @sock.route("/voice/ws")
    def handle_voice_websocket(ws: SimpleWebSocket):
        """
        Handle Twilio Media Streams WebSocket connection.

        This endpoint receives audio from Twilio, processes it through OpenAI Realtime API,
        and streams assistant audio back to Twilio.
        """
        import sys
        import traceback as tb
        print("=" * 50, file=sys.stderr, flush=True)
        print("WEBSOCKET HANDLER ENTERED", file=sys.stderr, flush=True)
        print("=" * 50, file=sys.stderr, flush=True)
        sys.stdout.flush()
        print("WebSocket connection opened for voice streaming", flush=True)

        # State for this connection
        call_sid: Optional[str] = None
        job_id: Optional[str] = None
        stream_sid: Optional[str] = None
        interaction_id: Optional[str] = None
        signup_mode: Optional[str] = None
        openai_ws = None
        forwarded_audio_frames = 0
        state_lock = threading.Lock()
        bridge_state = {
            "greeting_completed": False,
            "awaiting_response": False,
            "saw_openai_speech_event": False,
            "manual_vad_active": False,
            "manual_last_voice_ms": 0.0,
            "manual_last_trigger_ms": 0.0,
            "end_call_requested": False,
        }

        try:
            session_manager = get_session_manager()
            metrics_service = get_metrics_service()
            print("Session and metrics managers initialized", flush=True)
        except Exception as e:
            print(f"ERROR initializing managers: {e}", file=sys.stderr, flush=True)
            tb.print_exc()
            return

        message_count = 0

        try:
            print("Entering main receive loop...", flush=True)
            while True:
                # Receive message from Twilio
                try:
                    message = ws.receive()
                except Exception as recv_err:
                    print(f"Twilio ws.receive() error: {type(recv_err).__name__}: {recv_err}", flush=True)
                    break

                message_count += 1

                if message is None:
                    print(f"Received None message after {message_count} messages, breaking loop", flush=True)
                    break

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("event")

                with state_lock:
                    if bridge_state.get("end_call_requested"):
                        print("End-call requested; exiting Twilio receive loop", flush=True)
                        break

                # Log non-media events and periodic media count
                if event_type != "media":
                    print(f"[MSG #{message_count}] Twilio event: {event_type}", flush=True)
                elif message_count % 500 == 0:
                    print(f"[MSG #{message_count}] Received {message_count} media frames so far", flush=True)

                if event_type == "connected":
                    print(f"Twilio Media Stream connected: {data.get('protocol')}", flush=True)

                elif event_type == "start":
                    # Extract stream metadata
                    start_data = data.get("start", {})
                    stream_sid = start_data.get("streamSid")
                    call_sid = start_data.get("callSid")
                    custom_params = start_data.get("customParameters", {})
                    job_id = custom_params.get("job_id")

                    print(f"Stream started: stream_sid={stream_sid}, call_sid={call_sid}, job_id={job_id}", flush=True)

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
                            print(f"Error getting job context: {e}", flush=True)
                            import traceback
                            traceback.print_exc()

                    # Connect to OpenAI Realtime API
                    try:
                        print("Attempting to connect to OpenAI Realtime API...", flush=True)
                        openai_ws = _connect_openai_sync(signup_mode)
                        if openai_ws:
                            print("OpenAI connection successful, starting response handler thread...", flush=True)
                            # Start background thread to handle OpenAI responses
                            response_thread = threading.Thread(
                                target=_handle_openai_responses,
                                args=(openai_ws, ws, stream_sid, call_sid, metrics_service, bridge_state, state_lock),
                                daemon=True
                            )
                            response_thread.start()
                            print(f"Response handler thread started: {response_thread.name}", flush=True)

                            # Send initial greeting request
                            _send_greeting_request(openai_ws, signup_mode)
                            with state_lock:
                                bridge_state["awaiting_response"] = True
                        else:
                            print("OpenAI connection returned None; ending stream.", flush=True)
                            break
                    except Exception as e:
                        print(f"Error connecting to OpenAI: {e}", flush=True)
                        import traceback
                        traceback.print_exc()
                        print("Ending stream after OpenAI connection failure.", flush=True)
                        break

                elif event_type == "media":
                    # Process incoming audio from Twilio
                    media_data = data.get("media", {})
                    payload = media_data.get("payload")

                    if payload and openai_ws:
                        try:
                            # Forward Twilio μ-law payload directly to OpenAI.
                            # Session is configured with audio/pcmu input format.
                            audio_event = {
                                "type": "input_audio_buffer.append",
                                "audio": payload
                            }
                            openai_ws.send(json.dumps(audio_event))
                            forwarded_audio_frames += 1
                            if forwarded_audio_frames <= 5 or forwarded_audio_frames % 500 == 0:
                                print(
                                    f"Forwarded audio frame #{forwarded_audio_frames} to OpenAI",
                                    flush=True,
                                )

                            # Deterministic fallback: if OpenAI VAD isn't emitting speech events,
                            # use Twilio audio activity + silence gap to force commit/create.
                            now_ms = time.monotonic() * 1000.0
                            mulaw_audio = base64.b64decode(payload)
                            pcm_8k = mulaw_to_pcm16(mulaw_audio)
                            rms = _pcm16_rms(pcm_8k)
                            voice_threshold = 1000.0
                            silence_gap_ms = 1200.0
                            trigger_cooldown_ms = 1500.0

                            with state_lock:
                                greeting_completed = bridge_state["greeting_completed"]
                                awaiting_response = bridge_state["awaiting_response"]
                                saw_openai_speech_event = bridge_state["saw_openai_speech_event"]
                                manual_vad_active = bridge_state["manual_vad_active"]
                                manual_last_voice_ms = bridge_state["manual_last_voice_ms"]
                                manual_last_trigger_ms = bridge_state["manual_last_trigger_ms"]

                                if greeting_completed and not awaiting_response and not saw_openai_speech_event:
                                    if rms >= voice_threshold:
                                        bridge_state["manual_vad_active"] = True
                                        bridge_state["manual_last_voice_ms"] = now_ms
                                    elif manual_vad_active:
                                        silence_elapsed = now_ms - manual_last_voice_ms
                                        cooldown_elapsed = now_ms - manual_last_trigger_ms
                                        if silence_elapsed >= silence_gap_ms and cooldown_elapsed >= trigger_cooldown_ms:
                                            try:
                                                openai_ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                                                openai_ws.send(json.dumps({"type": "response.create"}))
                                                bridge_state["awaiting_response"] = True
                                                bridge_state["manual_vad_active"] = False
                                                bridge_state["manual_last_trigger_ms"] = now_ms
                                                print("Deterministic fallback triggered: commit + response.create", flush=True)
                                            except Exception as trigger_err:
                                                print(f"Deterministic fallback trigger error: {trigger_err}", flush=True)
                        except Exception as e:
                            print(f"Error forwarding audio to OpenAI: {e}")

                elif event_type == "stop":
                    print(f"Stream stopped: stream_sid={stream_sid}, total messages received: {message_count}", flush=True)
                    break

            print(f"Main Twilio loop exited normally after {message_count} messages", flush=True)

        except Exception as e:
            print(f"WebSocket error in main loop: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            print(f"Entering finally block, will close OpenAI connection. Total Twilio messages: {message_count}", flush=True)
            # Clean up
            if openai_ws:
                try:
                    print("Closing OpenAI WebSocket...", flush=True)
                    openai_ws.close()
                    print("OpenAI WebSocket closed", flush=True)
                except Exception as close_err:
                    print(f"Error closing OpenAI ws: {close_err}", flush=True)

            if call_sid:
                session_manager.end_session(call_sid)
                metrics_service.end_call(call_sid)

            print(f"WebSocket connection fully cleaned up for call_sid={call_sid}", flush=True)


def _start_keepalive_thread(ws, interval=20):
    """Start a background thread to send WebSocket pings to OpenAI."""
    import time

    def keepalive_loop():
        while True:
            try:
                time.sleep(interval)
                if ws.connected:
                    ws.ping()
                    print("Sent WebSocket ping to OpenAI", flush=True)
                else:
                    print("WebSocket disconnected, stopping keepalive thread", flush=True)
                    break
            except Exception as e:
                print(f"Keepalive thread error: {e}", flush=True)
                break

    keepalive_thread = threading.Thread(target=keepalive_loop, daemon=True)
    keepalive_thread.start()
    return keepalive_thread


def _connect_openai_sync(signup_mode: Optional[str]):
    """Connect to OpenAI Realtime API (synchronous wrapper)."""
    import os
    import websocket
    import ssl

    if not OPENAI_API_KEY:
        print("OpenAI API key not configured", flush=True)
        return None

    realtime_model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
    url = f"wss://api.openai.com/v1/realtime?model={realtime_model}"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    print(f"Connecting to OpenAI Realtime API...", flush=True)
    try:
        import socket

        # Create connection with keepalive options
        ws = websocket.create_connection(
            url,
            header=[f"{k}: {v}" for k, v in headers.items()],
            sslopt={"cert_reqs": ssl.CERT_REQUIRED},
            timeout=20,
            skip_utf8_validation=True,  # For binary audio data
            sockopt=[(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]  # Enable TCP keepalive
        )
        print("OpenAI WebSocket connected successfully", flush=True)
        # Avoid socket read timeouts during natural conversation pauses.
        ws.settimeout(None)

        # Keep TCP/WebSocket connection alive without mutating conversation state.
        _start_keepalive_thread(ws, interval=20)

        # Wait for session.created before sending any configuration
        print("Waiting for session.created from OpenAI...", flush=True)
        initial_message = ws.recv()
        if initial_message:
            initial_data = json.loads(initial_message)
            print(f"OpenAI initial event: {initial_data.get('type')}", flush=True)
            if initial_data.get("type") == "error":
                print(f"OpenAI error on connect: {initial_data.get('error')}", flush=True)
                ws.close()
                return None
        else:
            print("No initial message from OpenAI", flush=True)
            ws.close()
            return None

        # Now configure the session
        system_prompt = _get_system_prompt(signup_mode)
        session_config = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": realtime_model,
                "instructions": system_prompt,
                "output_modalities": ["audio"],
                "tools": [
                    {
                        "type": "function",
                        "name": "end_call",
                        "description": (
                            "Signal that this phone conversation is complete and should be terminated now. "
                            "Call this exactly once after your final goodbye."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "reason": {
                                    "type": "string",
                                    "enum": ["completed", "user_requested_end", "no_interest", "voicemail", "other"],
                                },
                                "summary": {"type": "string"},
                            },
                            "required": ["reason"],
                        },
                    }
                ],
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcmu"
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 900,
                            "idle_timeout_ms": 8000,
                            "create_response": True,
                            "interrupt_response": True
                        }
                    },
                    "output": {
                        "format": {
                            "type": "audio/pcmu"
                        },
                        "voice": "alloy"
                    }
                }
            }
        }
        ws.send(json.dumps(session_config))
        print("Session.update sent, waiting for session.updated...", flush=True)

        # Wait for session.updated confirmation
        update_message = ws.recv()
        if update_message:
            update_data = json.loads(update_message)
            print(f"OpenAI update response: {update_data.get('type')}", flush=True)
            if update_data.get("type") == "error":
                print(f"OpenAI session update error: {update_data.get('error')}", flush=True)
                ws.close()
                return None
            elif update_data.get("type") == "session.updated":
                print("OpenAI Realtime session configured successfully", flush=True)
        else:
            print("No update confirmation from OpenAI", flush=True)
            ws.close()
            return None

        return ws
    except Exception as e:
        print(f"Failed to connect to OpenAI Realtime: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return None


def _get_system_prompt(signup_mode: Optional[str]) -> str:
    """Get the system prompt for the qualification call."""
    if signup_mode in ("talent", "job_seeker", "executive", "candidate"):
        mode_context = "The user is an executive looking for job opportunities."
        greeting = "Hi, this is Ai-dan from ExecFlex. I noticed you just signed up looking for executive opportunities. Have I caught you at a bad time?"
    elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
        mode_context = "The user is looking to hire executive talent for their organization."
        greeting = "Hello, this is Ai-dan from ExecFlex. I noticed you just signed up looking for executive talent for your organization. Have I caught you at a bad time?"
    else:
        mode_context = "Determine whether the user is looking to hire executives or is an executive seeking opportunities."
        greeting = "Hello, this is Ai-dan from ExecFlex. I noticed you just signed up. Are you looking to hire executive talent, or are you an executive looking for opportunities?"

    return f"""You are Ai-dan, a friendly voice assistant for ExecFlex, a platform connecting companies with executive talent.

{mode_context}

IMPORTANT: Start the conversation IMMEDIATELY by saying: "{greeting}"

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
- When the call has clearly concluded, call the end_call tool exactly once.
- Do not repeat goodbye lines in a loop.
"""


def _send_greeting_request(openai_ws, signup_mode: Optional[str]):
    """Send initial greeting request to OpenAI."""
    create_response = {"type": "response.create"}
    print(f"Sending response.create to trigger greeting (signup_mode={signup_mode})", flush=True)
    openai_ws.send(json.dumps(create_response))
    print("Response.create sent to OpenAI", flush=True)


def _enable_post_greeting_barge_in(openai_ws):
    """Re-assert VAD turn behavior after greeting completes."""
    update_event = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "audio": {
                "input": {
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 900,
                        "idle_timeout_ms": 8000,
                        "create_response": True,
                        "interrupt_response": True
                    }
                }
            }
        }
    }
    openai_ws.send(json.dumps(update_event))


def _pcm16_rms(pcm16_data: bytes) -> float:
    """Compute RMS for PCM16 mono bytes."""
    if not pcm16_data:
        return 0.0
    sample_count = len(pcm16_data) // 2
    if sample_count <= 0:
        return 0.0
    samples = struct.unpack(f"<{sample_count}h", pcm16_data)
    energy = 0.0
    for s in samples:
        energy += float(s) * float(s)
    return (energy / float(sample_count)) ** 0.5


def _request_call_hangup(call_sid: Optional[str]) -> bool:
    """End a Twilio call immediately by CallSid."""
    if not call_sid:
        return False
    try:
        from config.clients import twilio_client
        if not twilio_client:
            print("Twilio client unavailable; cannot hang up call", flush=True)
            return False
        twilio_client.calls(call_sid).update(status="completed")
        print(f"Requested Twilio hangup for call_sid={call_sid}", flush=True)
        return True
    except Exception as e:
        print(f"Failed to request Twilio hangup for {call_sid}: {e}", flush=True)
        return False


def _handle_end_call_signal(item: dict, call_sid: str, bridge_state, state_lock, log_fn):
    """Detect end_call function invocation and trigger hangup once."""
    if (item or {}).get("type") != "function_call":
        return
    if (item or {}).get("name") != "end_call":
        return

    args_raw = item.get("arguments") or "{}"
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
    except Exception:
        args = {"raw_arguments": args_raw}

    with state_lock:
        if bridge_state.get("end_call_requested"):
            return
        bridge_state["end_call_requested"] = True

    log_fn(f"end_call tool invoked with args: {args}")
    _request_call_hangup(call_sid)


def _handle_openai_responses(openai_ws, twilio_ws, stream_sid: str, call_sid: str, metrics_service, bridge_state, state_lock):
    """Handle responses from OpenAI in a background thread."""
    import sys
    import websocket
    import os
    from datetime import datetime

    # Create a log file for this call
    log_dir = "/tmp"
    log_file = f"{log_dir}/openai_handler_{call_sid}.log"

    def log(msg):
        """Log to file, stdout, and stderr for reliability."""
        timestamp = datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg, flush=True)
        sys.stderr.write(f"{full_msg}\n")
        sys.stderr.flush()
        try:
            with open(log_file, "a") as f:
                f.write(f"{full_msg}\n")
                f.flush()
        except Exception:
            pass

    log(f"OpenAI response handler started for call {call_sid}")
    first_audio_recorded = False
    message_count = 0
    audio_chunks_sent = 0
    exit_reason = "unknown"
    greeting_completed = False

    try:
        while True:
            try:
                message = openai_ws.recv()
                if not message:
                    exit_reason = "empty_message"
                    log(f"OpenAI WebSocket returned empty message, exiting handler")
                    break

                message_count += 1
                data = json.loads(message)
                event_type = data.get("type")

                # Log all event types for debugging (first 50 messages, then key events only)
                if message_count <= 50 or event_type != "response.output_audio.delta":
                    log(f"OpenAI event #{message_count}: {event_type}")
                    # Log full data for key events
                    if event_type in ("error", "response.done", "session.updated", "response.created", "response.output_audio.done", "response.output_item.done"):
                        log(f"  Full data: {json.dumps(data)[:800]}")

                if event_type == "response.output_audio.delta":
                    # Streaming audio from OpenAI
                    audio_b64 = data.get("delta", "")
                    if audio_b64:
                        # Record first audio timing
                        if not first_audio_recorded:
                            metrics_service.record_first_audio(call_sid)
                            first_audio_recorded = True
                            log(f"First audio chunk received from OpenAI!")

                        # OpenAI is configured to output audio/pcmu.
                        # Twilio media payload expects base64 μ-law bytes, so pass through.
                        try:
                            media_event = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": audio_b64
                                }
                            }
                            twilio_ws.send(json.dumps(media_event))
                            audio_chunks_sent += 1
                            if audio_chunks_sent <= 5 or audio_chunks_sent % 50 == 0:
                                log(f"Sent audio chunk #{audio_chunks_sent} to Twilio")
                        except Exception as e:
                            log(f"Error sending audio to Twilio: {type(e).__name__}: {e}")
                            # Don't break - Twilio might have disconnected but we can still process OpenAI events

                elif event_type == "response.output_audio.done":
                    # Response complete
                    metrics_service.record_response_complete(call_sid)
                    log(f"Response audio complete, sent {audio_chunks_sent} audio chunks total")
                    first_audio_recorded = False  # Reset for next turn

                elif event_type == "input_audio_buffer.speech_stopped":
                    # User stopped speaking - record timing
                    metrics_service.record_user_speech_end(call_sid)
                    log("User stopped speaking")
                    with state_lock:
                        bridge_state["saw_openai_speech_event"] = True

                elif event_type == "input_audio_buffer.speech_started":
                    log("User started speaking")
                    with state_lock:
                        bridge_state["saw_openai_speech_event"] = True

                elif event_type == "input_audio_buffer.committed":
                    log("Input audio buffer committed")

                elif event_type == "conversation.item.created":
                    item = data.get("item", {}) or {}
                    log(
                        f"Conversation item created: type={item.get('type')}, "
                        f"role={item.get('role')}, id={item.get('id')}"
                    )

                elif event_type == "response.output_item.done":
                    item = data.get("item", {}) or {}
                    _handle_end_call_signal(item, call_sid, bridge_state, state_lock, log)

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    # Got transcript of user speech
                    transcript = data.get("transcript", "")
                    log(f"User said: {transcript}")

                elif event_type == "error":
                    error = data.get("error", {})
                    log(f"OpenAI error: {error}")
                    metrics_service.record_event(
                        call_sid,
                        "openai_error",
                        status="error",
                        provider="openai",
                        metadata=error
                    )
                    exit_reason = f"openai_error: {error.get('type', 'unknown')}"

                elif event_type == "response.done":
                    response = data.get("response", {}) or {}
                    for output_item in response.get("output", []) or []:
                        _handle_end_call_signal(output_item, call_sid, bridge_state, state_lock, log)

                    with state_lock:
                        bridge_state["awaiting_response"] = False
                        bridge_state["saw_openai_speech_event"] = False
                        bridge_state["manual_vad_active"] = False

                    # Flip to explicit post-greeting turn behavior.
                    if not greeting_completed:
                        greeting_completed = True
                        with state_lock:
                            bridge_state["greeting_completed"] = True
                        try:
                            _enable_post_greeting_barge_in(openai_ws)
                            log("Post-greeting barge-in mode enabled")
                        except Exception as e:
                            log(f"Failed to enable post-greeting barge-in mode: {type(e).__name__}: {e}")

                elif event_type == "response.created":
                    with state_lock:
                        bridge_state["awaiting_response"] = True

            except websocket.WebSocketConnectionClosedException as e:
                exit_reason = f"websocket_closed: {e}"
                log(f"OpenAI WebSocket connection closed: {e}")
                break
            except websocket.WebSocketTimeoutException as e:
                exit_reason = f"websocket_timeout: {e}"
                log(f"OpenAI WebSocket timeout: {e}")
                continue
            except Exception as e:
                import traceback
                exit_reason = f"exception: {type(e).__name__}: {e}"
                log(f"Error handling OpenAI message: {type(e).__name__}: {e}")
                traceback.print_exc()
                break

    except Exception as e:
        import traceback
        exit_reason = f"outer_exception: {type(e).__name__}: {e}"
        log(f"OpenAI response handler error: {type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        log(f"OpenAI response handler exiting for call {call_sid}")
        log(f"  Exit reason: {exit_reason}")
        log(f"  Processed {message_count} messages, sent {audio_chunks_sent} audio chunks")


