"""
WebSocket route handler for Twilio Media Streams.
This module is initialized from server.py with the Flask-Sock instance.
"""
import json
import base64
import threading
import time
import struct
import os
import re
from typing import Optional
from flask_sock import Sock
from simple_websocket import Server as SimpleWebSocket

from services.realtime_session_state import get_session_manager, CallPhase
from services.voice_metrics import get_metrics_service
from services.platform_config_service import get_bool_config, get_number_config, get_string_config
from config.app_config import OPENAI_API_KEY, ELEVEN_API_KEY, ELEVEN_VOICE_ID

# Import the bridge components
from services.realtime_voice_bridge import (
    mulaw_to_pcm16,
)

VOICE_MANUAL_VAD_FALLBACK_ENABLED = os.getenv("VOICE_MANUAL_VAD_FALLBACK", "0") == "1"

def _append_job_debug_event(job_id: Optional[str], event_name: str, metadata: Optional[dict] = None):
    """Persist lightweight websocket lifecycle events to outbound_call_jobs.artifacts."""
    if not job_id:
        return
    try:
        from config.clients import supabase_client
        from datetime import datetime, timezone
        if not supabase_client:
            return
        existing = (
            supabase_client.table("outbound_call_jobs")
            .select("artifacts")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return
        artifacts = (existing.data[0] or {}).get("artifacts", {}) or {}
        events = artifacts.get("debug_events", [])
        if not isinstance(events, list):
            events = []
        events.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_name,
            "meta": metadata or {},
        })
        artifacts["debug_events"] = events[-40:]
        supabase_client.table("outbound_call_jobs").update({"artifacts": artifacts}).eq("id", job_id).execute()
    except Exception as exc:
        print(f"Failed to append debug event for job {job_id}: {exc}", flush=True)


def _render_prompt_template(template: str, variables: dict) -> str:
    """Render {var_name} and {var_name|fallback} placeholders."""
    if not template:
        return template

    if not isinstance(variables, dict):
        variables = {}

    def _replace(match):
        key = match.group(1)
        fallback = match.group(2)
        value = variables.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
        if fallback is not None:
            return fallback
        # For name placeholders, use a natural default instead of reading braces aloud.
        if key in ("first_name", "user_name"):
            return "there"
        # Remove unresolved placeholders to avoid awkward speech output.
        return ""

    rendered = re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?:\|([^{}]*))?\}", _replace, template)
    return re.sub(r"\s{2,}", " ", rendered).strip()


def _load_vad_config(job_id: Optional[str]) -> dict:
    """Load VAD tuning from platform_config for new calls."""
    threshold, _, _ = get_number_config("voice_vad_threshold", default=0.5)
    prefix_padding_ms, _, _ = get_number_config("voice_vad_prefix_padding_ms", default=300)
    silence_duration_ms, _, _ = get_number_config("voice_vad_silence_duration_ms", default=900)
    # Keep idle timeout generous to avoid cutting callers off mid-answer.
    idle_timeout_ms, _, _ = get_number_config("voice_vad_idle_timeout_ms", default=0)
    config = {
        "type": "server_vad",
        "threshold": float(threshold),
        "prefix_padding_ms": int(prefix_padding_ms),
        "silence_duration_ms": int(silence_duration_ms),
        "create_response": True,
        "interrupt_response": True,
    }
    if float(idle_timeout_ms) > 0:
        config["idle_timeout_ms"] = int(idle_timeout_ms)
    else:
        # Allow explicit disable via platform_config (0 or negative).
        _append_job_debug_event(job_id, "vad_idle_timeout_disabled")
    _append_job_debug_event(job_id, "vad_config_loaded", config)
    return config


def _is_low_signal_user_transcript(
    text: str,
    *,
    min_chars: int = 4,
    allowed_short_replies: Optional[set[str]] = None,
) -> bool:
    """Heuristic guard for noise/partial artifacts that should not trigger a full assistant turn."""
    normalized = re.sub(r"[^a-z0-9' ]+", "", (text or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return True

    # Common short valid replies should pass through.
    if allowed_short_replies is None:
        allowed_short_replies = {
            "yes", "no", "yep", "yeah", "nope", "ok", "okay", "sure", "both", "ja"
        }
    if normalized in allowed_short_replies:
        return False

    # Filler/noise-like artifacts, including observed false transcript "ani".
    likely_noise_tokens = {"uh", "um", "erm", "mm", "hmm", "mhm", "ah", "eh", "ani"}
    if normalized in likely_noise_tokens:
        return True

    # Single short token that isn't a common valid acknowledgement is usually noise.
    if " " not in normalized and len(normalized) < max(1, int(min_chars)):
        return True

    return False


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
            "assistant_playback_active": False,
            "assistant_playback_block_until_ms": 0.0,
            "playback_input_cooldown_ms": 0,
            "overlap_guard_ms": 600,
            "playback_dropped_frames": 0,
            "openai_turn_detection_muted": False,
            "last_assistant_audio_done_ms": 0.0,
            "low_signal_filter_enabled": True,
            "low_signal_min_chars": 4,
            "low_signal_allowed_short_replies": {
                "yes", "no", "yep", "yeah", "nope", "ok", "okay", "sure", "both", "ja"
            },
            "end_call_min_turns": 2,
            "next_transcript_turn_sequence": 1,
            "last_transcript_key": None,
            "use_elevenlabs_output": False,
            "assistant_text_parts": [],
            "prompt_vars": {},
            "vad_config": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 900,
                "idle_timeout_ms": 30000,
                "create_response": True,
                "interrupt_response": True,
            },
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
                    prompt_vars = {}

                    print(f"Stream started: stream_sid={stream_sid}, call_sid={call_sid}, job_id={job_id}", flush=True)
                    _append_job_debug_event(job_id, "twilio_stream_start", {
                        "stream_sid": stream_sid,
                        "call_sid": call_sid,
                        "manual_vad_fallback_enabled": VOICE_MANUAL_VAD_FALLBACK_ENABLED,
                    })

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
                                prompt_vars = {}
                                user_name = None
                                first_name = None
                                try:
                                    profile_resp = supabase_client.table("people_profiles")\
                                        .select("first_name, last_name")\
                                        .eq("user_id", job.get("user_id"))\
                                        .limit(1)\
                                        .execute()
                                    if profile_resp.data:
                                        profile = profile_resp.data[0] or {}
                                        first_name = (profile.get("first_name") or "").strip() or None
                                        last_name = (profile.get("last_name") or "").strip()
                                        if first_name or last_name:
                                            user_name = f"{first_name or ''} {last_name or ''}".strip()
                                except Exception as profile_exc:
                                    print(f"Failed loading profile for prompt variables: {profile_exc}", flush=True)
                                if not first_name or not user_name:
                                    try:
                                        auth_user_resp = supabase_client.schema("auth").table("users")\
                                            .select("raw_user_meta_data")\
                                            .eq("id", job.get("user_id"))\
                                            .limit(1)\
                                            .execute()
                                        if auth_user_resp.data:
                                            raw_meta = (auth_user_resp.data[0] or {}).get("raw_user_meta_data") or {}
                                            auth_first_name = (raw_meta.get("first_name") or "").strip()
                                            auth_full_name = (raw_meta.get("full_name") or raw_meta.get("name") or "").strip()
                                            if not first_name and auth_first_name:
                                                first_name = auth_first_name
                                            if not user_name and auth_full_name:
                                                user_name = auth_full_name
                                            if not user_name and first_name:
                                                user_name = first_name
                                    except Exception as auth_user_exc:
                                        print(f"Failed loading auth user metadata for prompt variables: {auth_user_exc}", flush=True)
                                if user_name:
                                    prompt_vars["user_name"] = user_name
                                if first_name:
                                    prompt_vars["first_name"] = first_name

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

                    use_elevenlabs_output = False
                    enabled, _, _ = get_bool_config("elevenlabs_output_enabled", default=False)
                    preflight_timeout_ms, _, _ = get_number_config(
                        "voice_elevenlabs_preflight_timeout_ms",
                        default=200,
                    )
                    if enabled:
                        use_elevenlabs_output = _preflight_elevenlabs_ws(
                            timeout_ms=max(100, int(preflight_timeout_ms))
                        )
                        if not use_elevenlabs_output:
                            print("ElevenLabs preflight failed, pinning call to OpenAI audio output", flush=True)
                    _append_job_debug_event(job_id, "voice_routing_selected", {
                        "elevenlabs_flag_enabled": bool(enabled),
                        "use_elevenlabs_output": bool(use_elevenlabs_output),
                    })
                    with state_lock:
                        bridge_state["use_elevenlabs_output"] = use_elevenlabs_output
                        bridge_state["assistant_text_parts"] = []
                        bridge_state["vad_config"] = _load_vad_config(job_id)
                        bridge_state["prompt_vars"] = prompt_vars
                        playback_input_cooldown_ms, _, _ = get_number_config(
                            "voice_playback_input_cooldown_ms",
                            default=0,
                        )
                        bridge_state["playback_input_cooldown_ms"] = max(
                            0,
                            int(playback_input_cooldown_ms),
                        )
                        overlap_guard_ms, _, _ = get_number_config(
                            "voice_overlap_guard_ms",
                            default=600,
                        )
                        bridge_state["overlap_guard_ms"] = max(0, int(overlap_guard_ms))
                        low_signal_filter_enabled, _, _ = get_bool_config(
                            "voice_low_signal_filter_enabled",
                            default=True,
                        )
                        low_signal_min_chars, _, _ = get_number_config(
                            "voice_low_signal_min_chars",
                            default=4,
                        )
                        allowed_short_replies_raw, _, _ = get_string_config(
                            "voice_low_signal_allowed_short_replies",
                            "yes,no,yep,yeah,nope,ok,okay,sure,both,ja",
                        )
                        end_call_min_turns, _, _ = get_number_config(
                            "voice_end_call_min_turns",
                            default=2,
                        )
                        allowed_short_replies = {
                            token.strip().lower()
                            for token in str(allowed_short_replies_raw).split(",")
                            if token and token.strip()
                        }
                        bridge_state["low_signal_filter_enabled"] = bool(low_signal_filter_enabled)
                        bridge_state["low_signal_min_chars"] = max(1, int(low_signal_min_chars))
                        bridge_state["low_signal_allowed_short_replies"] = (
                            allowed_short_replies
                            or {"yes", "no", "ok", "sure"}
                        )
                        bridge_state["end_call_min_turns"] = max(0, int(end_call_min_turns))

                    # Connect to OpenAI Realtime API
                    try:
                        print("Attempting to connect to OpenAI Realtime API...", flush=True)
                        _append_job_debug_event(job_id, "openai_connect_attempt")
                        openai_ws = _connect_openai_sync(
                            signup_mode,
                            output_text_only=use_elevenlabs_output,
                            job_id=job_id,
                            vad_config=bridge_state.get("vad_config"),
                            prompt_vars=bridge_state.get("prompt_vars"),
                        )
                        if openai_ws:
                            _append_job_debug_event(job_id, "openai_connect_success")
                            print("OpenAI connection successful, starting response handler thread...", flush=True)
                            # Start background thread to handle OpenAI responses
                            response_thread = threading.Thread(
                                target=_handle_openai_responses,
                                args=(openai_ws, ws, stream_sid, call_sid, interaction_id, metrics_service, bridge_state, state_lock, job_id),
                                daemon=True
                            )
                            response_thread.start()
                            print(f"Response handler thread started: {response_thread.name}", flush=True)

                            # Send initial greeting request
                            _send_greeting_request(openai_ws, signup_mode)
                            _append_job_debug_event(job_id, "greeting_request_sent")
                            with state_lock:
                                bridge_state["awaiting_response"] = True
                        else:
                            print("OpenAI connection returned None; ending stream.", flush=True)
                            _append_job_debug_event(job_id, "openai_connect_none")
                            break
                    except Exception as e:
                        print(f"Error connecting to OpenAI: {e}", flush=True)
                        import traceback
                        traceback.print_exc()
                        print("Ending stream after OpenAI connection failure.", flush=True)
                        _append_job_debug_event(job_id, "openai_connect_exception", {"error": str(e)})
                        break

                elif event_type == "media":
                    # Process incoming audio from Twilio
                    media_data = data.get("media", {})
                    payload = media_data.get("payload")

                    if payload and openai_ws:
                        try:
                            now_ms = time.monotonic() * 1000.0
                            with state_lock:
                                assistant_playback_active = bool(
                                    bridge_state.get("assistant_playback_active")
                                )
                                assistant_playback_block_until_ms = float(
                                    bridge_state.get("assistant_playback_block_until_ms", 0.0)
                                )
                            if assistant_playback_active or now_ms < assistant_playback_block_until_ms:
                                # Ignore caller audio while assistant audio is playing (and for a brief tail cooldown)
                                # to prevent immediate follow-up responses from overlap acknowledgments.
                                with state_lock:
                                    bridge_state["playback_dropped_frames"] = int(
                                        bridge_state.get("playback_dropped_frames", 0)
                                    ) + 1
                                continue

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

                            if VOICE_MANUAL_VAD_FALLBACK_ENABLED:
                                # Deterministic fallback (opt-in): if OpenAI VAD isn't emitting speech events,
                                # use Twilio audio activity + silence gap to force commit/create.
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
                    _append_job_debug_event(job_id, "twilio_stream_stop", {"message_count": message_count})
                    break

            print(f"Main Twilio loop exited normally after {message_count} messages", flush=True)

        except Exception as e:
            print(f"WebSocket error in main loop: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            print(f"Entering finally block, will close OpenAI connection. Total Twilio messages: {message_count}", flush=True)
            _append_job_debug_event(job_id, "voice_ws_finally", {"message_count": message_count, "call_sid": call_sid})
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


def _connect_openai_sync(
    signup_mode: Optional[str],
    output_text_only: bool = False,
    job_id: Optional[str] = None,
    vad_config: Optional[dict] = None,
    prompt_vars: Optional[dict] = None,
):
    """Connect to OpenAI Realtime API (synchronous wrapper)."""
    import os
    import websocket
    import ssl

    if not OPENAI_API_KEY:
        print("OpenAI API key not configured", flush=True)
        return None

    realtime_model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
    realtime_voice = os.getenv("OPENAI_REALTIME_VOICE", "ash")
    effective_vad = vad_config or {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 900,
        "idle_timeout_ms": 30000,
        "create_response": True,
        "interrupt_response": True,
    }
    url = f"wss://api.openai.com/v1/realtime?model={realtime_model}"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }

    print(f"Connecting to OpenAI Realtime API...", flush=True)
    try:
        import socket

        # Create connection with retries for transient upstream handshake errors.
        ws = None
        connect_err = None
        max_connect_attempts = 3
        for attempt in range(1, max_connect_attempts + 1):
            try:
                ws = websocket.create_connection(
                    url,
                    header=[f"{k}: {v}" for k, v in headers.items()],
                    sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                    timeout=20,
                    skip_utf8_validation=True,  # For binary audio data
                    sockopt=[(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)],  # Enable TCP keepalive
                )
                if attempt > 1:
                    _append_job_debug_event(job_id, "openai_connect_retry_success", {"attempt": attempt})
                break
            except Exception as connect_exc:
                connect_err = connect_exc
                _append_job_debug_event(
                    job_id,
                    "openai_connect_retry_error",
                    {"attempt": attempt, "error": str(connect_exc)},
                )
                if attempt >= max_connect_attempts:
                    raise
                time.sleep(0.4 * attempt)
        if ws is None:
            raise RuntimeError(f"OpenAI connect failed after retries: {connect_err}")
        print("OpenAI WebSocket connected successfully", flush=True)
        # Avoid socket read timeouts during natural conversation pauses.
        ws.settimeout(None)

        # Keep TCP/WebSocket connection alive without mutating conversation state.
        _start_keepalive_thread(ws, interval=20)

        # Wait for session.created (or early error) before sending any configuration.
        print("Waiting for session.created from OpenAI...", flush=True)
        saw_session_created = False
        for _ in range(20):
            initial_message = ws.recv()
            if not initial_message:
                continue
            initial_data = json.loads(initial_message)
            event_type = initial_data.get("type")
            print(f"OpenAI initial event: {event_type}", flush=True)
            if event_type == "error":
                print(f"OpenAI error on connect: {initial_data.get('error')}", flush=True)
                _append_job_debug_event(job_id, "openai_connect_error", {"stage": "connect", "error": initial_data.get("error")})
                ws.close()
                return None
            if event_type == "session.created":
                saw_session_created = True
                break
        if not saw_session_created:
            print("Did not receive session.created from OpenAI", flush=True)
            _append_job_debug_event(job_id, "openai_connect_error", {"stage": "session_created_timeout"})
            ws.close()
            return None

        # Now configure the session
        system_prompt = _get_system_prompt(signup_mode, prompt_vars)
        session_config = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": realtime_model,
                "instructions": system_prompt,
                "output_modalities": ["text"] if output_text_only else ["audio"],
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
                        "format": {"type": "audio/pcmu"},
                        "transcription": {
                            "model": "gpt-4o-mini-transcribe"
                        },
                        "turn_detection": effective_vad,
                    },
                    "output": {
                        "format": {"type": "audio/pcmu"},
                        "voice": realtime_voice
                    }
                },
            }
        }

        ws.send(json.dumps(session_config))
        print("Session.update sent, waiting for session.updated...", flush=True)
        saw_session_updated = False
        for _ in range(30):
            update_message = ws.recv()
            if not update_message:
                continue
            update_data = json.loads(update_message)
            event_type = update_data.get("type")
            print(f"OpenAI update response: {event_type}", flush=True)
            if event_type == "error":
                _append_job_debug_event(
                    job_id,
                    "openai_connect_error",
                    {"stage": "session_update", "error": update_data.get("error")},
                )
                break
            if event_type == "session.updated":
                saw_session_updated = True
                print("OpenAI Realtime session configured successfully", flush=True)
                break
        if not saw_session_updated:
            print("No session.updated confirmation from OpenAI", flush=True)
            ws.close()
            return None

        return ws
    except Exception as e:
        print(f"Failed to connect to OpenAI Realtime: {e}", flush=True)
        _append_job_debug_event(job_id, "openai_connect_error", {"stage": "exception", "error": str(e)})
        import traceback
        traceback.print_exc()
        return None


DEFAULT_TALENT_GREETING = (
    "Hi, this is A I Dan from ExecFlex. I noticed you just signed up looking for executive opportunities. "
    "Have I caught you at a bad time?"
)
DEFAULT_COMPANY_GREETING = (
    "Hello, this is A I Dan from ExecFlex. I noticed you just signed up looking for executive talent for your organization. "
    "Have I caught you at a bad time?"
)
DEFAULT_FALLBACK_GREETING = (
    "Hello, this is A I Dan from ExecFlex. I noticed you just signed up. "
    "Are you looking to hire executive talent, or are you an executive looking for opportunities?"
)
DEFAULT_GENERAL_SYSTEM_PROMPT = """CONVERSATION STYLE:
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
6. Be witty.
7. To progress up the levels of conversation from cliche, to facts, to opinions, to feelings, to needs/identity (dreams)

IMPORTANT RULES:
- Never ask for information already provided
- If the user wants to end the call, thank them politely and close
- After 8-10 minutes or when enough info is gathered, begin closing the conversation
- Be natural and conversational, not robotic
- When the call has clearly concluded, call the end_call tool exactly once.
- Do not repeat goodbye lines in a loop.
- Use Mirroring if they dont seem quite finished. Repeat back the last few words of what they said without embellishment in an upward tone.
- Use Labelling of the potential emption, if they express an opinion or feeling. e.g. 'That sounds like it was exciting!'"""


def _get_system_prompt(signup_mode: Optional[str], prompt_vars: Optional[dict] = None) -> str:
    """Get the system prompt for the qualification call."""
    talent_greeting, _, _ = get_string_config("voice_prompt_talent_greeting", DEFAULT_TALENT_GREETING)
    company_greeting, _, _ = get_string_config("voice_prompt_company_greeting", DEFAULT_COMPANY_GREETING)
    fallback_greeting, _, _ = get_string_config("voice_prompt_fallback_greeting", DEFAULT_FALLBACK_GREETING)
    general_prompt, _, _ = get_string_config("voice_prompt_general_system", DEFAULT_GENERAL_SYSTEM_PROMPT)
    first_turn_max_words, _, _ = get_number_config("voice_first_turn_max_words", default=45)

    if signup_mode in ("talent", "job_seeker", "executive", "candidate"):
        mode_context = "The user is an executive looking for job opportunities."
        greeting = talent_greeting
    elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
        mode_context = "The user is looking to hire executive talent for their organization."
        greeting = company_greeting
    else:
        mode_context = "Determine whether the user is looking to hire executives or is an executive seeking opportunities."
        greeting = fallback_greeting
    greeting = _render_prompt_template(greeting, prompt_vars or {})
    general_prompt = _render_prompt_template(general_prompt, prompt_vars or {})

    return f"""You are Ai-dan, a friendly voice assistant for ExecFlex, a platform connecting companies with executive talent.

{mode_context}

IMPORTANT: Start the conversation IMMEDIATELY by saying: "{greeting}"
IMPORTANT: For your first spoken turn only, keep your total response under {max(10, int(first_turn_max_words))} words.

{general_prompt}
"""


def _send_greeting_request(openai_ws, signup_mode: Optional[str]):
    """Send initial greeting request to OpenAI."""
    create_response = {"type": "response.create"}
    print(f"Sending response.create to trigger greeting (signup_mode={signup_mode})", flush=True)
    openai_ws.send(json.dumps(create_response))
    print("Response.create sent to OpenAI", flush=True)


def _enable_post_greeting_barge_in(openai_ws, vad_config: Optional[dict] = None):
    """Re-assert VAD turn behavior after greeting completes."""
    effective_vad = vad_config or {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 900,
        "idle_timeout_ms": 30000,
        "create_response": True,
        "interrupt_response": True,
    }
    update_event = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "audio": {
                "input": {
                    "turn_detection": effective_vad
                }
            },
        }
    }
    openai_ws.send(json.dumps(update_event))


def _set_turn_detection_mode(
    openai_ws,
    *,
    vad_config: Optional[dict] = None,
    create_response: bool,
    interrupt_response: bool,
):
    """Update OpenAI turn detection flags for playback gating."""
    base_vad = vad_config or {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 900,
        "idle_timeout_ms": 30000,
        "create_response": True,
        "interrupt_response": True,
    }
    effective_vad = dict(base_vad)
    effective_vad["create_response"] = bool(create_response)
    effective_vad["interrupt_response"] = bool(interrupt_response)
    update_event = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "audio": {
                "input": {
                    "turn_detection": effective_vad
                }
            },
        },
    }
    openai_ws.send(json.dumps(update_event))


def _clear_input_audio_buffer(openai_ws):
    """Clear any pending user audio currently buffered in OpenAI."""
    openai_ws.send(json.dumps({"type": "input_audio_buffer.clear"}))


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


def _request_call_hangup_with_message(call_sid: Optional[str], message: str) -> bool:
    """End a Twilio call with a short spoken message."""
    if not call_sid:
        return False
    try:
        from config.clients import twilio_client
        if not twilio_client:
            return False
        safe_message = (message or "").replace("&", " and ").replace("<", "").replace(">", "")
        twiml = (
            f"<Response><Say voice=\"alice\" language=\"en-GB\">{safe_message}</Say>"
            "<Hangup/></Response>"
        )
        twilio_client.calls(call_sid).update(twiml=twiml)
        print(f"Requested Twilio hangup-with-message for call_sid={call_sid}", flush=True)
        return True
    except Exception as e:
        print(f"Failed hangup-with-message for {call_sid}: {e}", flush=True)
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
        turn_count = int(bridge_state.get("next_transcript_turn_sequence", 1))
        last_assistant_audio_done_ms = float(bridge_state.get("last_assistant_audio_done_ms", 0.0))
        end_call_min_turns = int(bridge_state.get("end_call_min_turns", 2))

    # Guardrail: ignore accidental early end_call tool invocations.
    reason = (args.get("reason") or "").strip().lower() if isinstance(args, dict) else ""
    allow_early_reasons = {"user_requested_end", "no_interest", "voicemail"}
    if turn_count <= end_call_min_turns and reason not in allow_early_reasons:
        log_fn(f"Ignoring early end_call signal (turn_count={turn_count}, reason={reason or 'unknown'})")
        return

    with state_lock:
        if bridge_state.get("end_call_requested"):
            return
        bridge_state["end_call_requested"] = True

    log_fn(f"end_call tool invoked with args: {args}")
    min_grace_ms = 3000
    try:
        configured_grace_ms, _, _ = get_number_config("voice_end_call_grace_ms", default=min_grace_ms)
        min_grace_ms = max(0, int(configured_grace_ms))
    except Exception:
        pass
    now_ms = time.monotonic() * 1000.0
    elapsed_since_audio_done_ms = now_ms - last_assistant_audio_done_ms if last_assistant_audio_done_ms > 0 else 0.0
    remaining_wait_ms = max(0, min_grace_ms - int(elapsed_since_audio_done_ms))
    if remaining_wait_ms > 0:
        log_fn(f"Delaying hangup {remaining_wait_ms}ms so final assistant audio can finish playing")
        time.sleep(remaining_wait_ms / 1000.0)
    _request_call_hangup(call_sid)


def _preflight_elevenlabs_ws(timeout_ms: int = 1000) -> bool:
    """Quick call-start check for ElevenLabs websocket availability."""
    if not ELEVEN_API_KEY or not ELEVEN_VOICE_ID:
        return False
    try:
        import websocket
        ws_url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}/stream-input"
            "?model_id=eleven_turbo_v2_5&output_format=ulaw_8000"
        )
        timeout_s = max(timeout_ms, 100) / 1000.0
        ws = websocket.create_connection(
            ws_url,
            timeout=timeout_s,
            header=[f"xi-api-key: {ELEVEN_API_KEY}"],
        )
        ws.close()
        return True
    except Exception as exc:
        print(f"ElevenLabs preflight failed: {exc}", flush=True)
        return False


def _extract_assistant_text(response_payload: dict, fallback_parts: list[str]) -> str:
    """Extract assistant text from OpenAI response.done payload."""
    joined = "".join(fallback_parts or []).strip()
    if joined:
        return joined
    response = (response_payload or {}).get("response", {}) or {}
    for output_item in response.get("output", []) or []:
        if not isinstance(output_item, dict):
            continue
        for content_item in output_item.get("content", []) or []:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text") or content_item.get("transcript")
            if text:
                return str(text).strip()
    return ""


def _stream_text_via_elevenlabs_to_twilio(
    *,
    text: str,
    twilio_ws,
    stream_sid: str,
    call_sid: str,
    metrics_service,
    log_fn,
) -> bool:
    """Synthesize assistant text with ElevenLabs and stream audio chunks to Twilio."""
    if not text:
        return True
    if not ELEVEN_API_KEY or not ELEVEN_VOICE_ID:
        log_fn("ElevenLabs credentials missing")
        return False

    import websocket

    ws_url = (
        f"wss://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}/stream-input"
        "?model_id=eleven_turbo_v2_5&output_format=ulaw_8000"
    )

    max_attempts = 2
    first_chunk_timeout_s = 1.2
    total_timeout_s = 15.0

    for attempt in range(1, max_attempts + 1):
        eleven_ws = None
        first_audio_recorded = False
        audio_chunks_sent = 0
        started_at = time.monotonic()
        first_chunk_deadline = started_at + first_chunk_timeout_s
        try:
            eleven_ws = websocket.create_connection(
                ws_url,
                timeout=8,
                header=[f"xi-api-key: {ELEVEN_API_KEY}"],
            )
            eleven_ws.settimeout(1.0)
            eleven_ws.send(json.dumps({
                "text": " ",
                "voice_settings": {
                    "stability": 0.35,
                    "similarity_boost": 0.9,
                },
            }))
            eleven_ws.send(json.dumps({
                "text": text,
                "try_trigger_generation": True,
            }))
            eleven_ws.send(json.dumps({"text": ""}))

            while True:
                now = time.monotonic()
                if now - started_at > total_timeout_s:
                    raise TimeoutError("ElevenLabs stream exceeded total timeout")
                if not first_audio_recorded and now > first_chunk_deadline:
                    raise TimeoutError("ElevenLabs first audio chunk timeout")

                try:
                    raw = eleven_ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                payload = json.loads(raw)
                if payload.get("error"):
                    raise RuntimeError(f"ElevenLabs error: {payload.get('error')}")

                audio_b64 = payload.get("audio")
                if audio_b64:
                    if not first_audio_recorded:
                        metrics_service.record_first_audio(call_sid)
                        first_audio_recorded = True
                    twilio_ws.send(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": audio_b64},
                    }))
                    audio_chunks_sent += 1
                if payload.get("isFinal"):
                    break

            metrics_service.record_response_complete(call_sid)
            log_fn(
                f"ElevenLabs response complete (attempt {attempt}), sent {audio_chunks_sent} audio chunks"
            )
            return True
        except Exception as exc:
            log_fn(
                f"ElevenLabs streaming attempt {attempt}/{max_attempts} failed: "
                f"{type(exc).__name__}: {exc}"
            )
            if attempt >= max_attempts:
                return False
        finally:
            if eleven_ws:
                try:
                    eleven_ws.close()
                except Exception:
                    pass

    return False


def _fallback_to_openai_audio_mode(openai_ws, assistant_text: str, bridge_state, state_lock, log_fn) -> bool:
    """Disable ElevenLabs mode for this call and continue with OpenAI audio output."""
    realtime_voice = os.getenv("OPENAI_REALTIME_VOICE", "ash")
    session_update = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "audio": {
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": realtime_voice,
                }
            }
        },
    }
    try:
        openai_ws.send(json.dumps(session_update))
        with state_lock:
            bridge_state["use_elevenlabs_output"] = False
            bridge_state["assistant_text_parts"] = []
        if assistant_text:
            recovery_response = {
                "type": "response.create",
                "response": {
                    "instructions": (
                        "Briefly repeat your previous reply to the caller in one concise sentence. "
                        f"Previous reply: {assistant_text}"
                    )
                },
            }
            openai_ws.send(json.dumps(recovery_response))
        log_fn("Fell back to OpenAI audio mode for this call")
        return True
    except Exception as exc:
        log_fn(f"Failed to switch to OpenAI audio mode: {type(exc).__name__}: {exc}")
        return False


def _persist_transcript_turn(interaction_id: Optional[str], speaker: str, text: str, turn_sequence: int, raw_payload: dict) -> None:
    """Persist a transcript turn row for realtime voice calls."""
    if not interaction_id or not text:
        return
    try:
        from config.clients import supabase_client
        supabase_client.table("interaction_turns").insert({
            "interaction_id": interaction_id,
            "speaker": speaker,
            "text": text,
            "turn_sequence": turn_sequence,
            "raw_payload": raw_payload or {},
        }).execute()
    except Exception as e:
        print(
            f"Failed to persist transcript turn interaction_id={interaction_id} "
            f"speaker={speaker} turn_sequence={turn_sequence}: {e}",
            flush=True,
        )


def _store_transcript_turn(interaction_id: Optional[str], speaker: str, text: str, raw_payload: dict, bridge_state, state_lock, log_fn):
    """Allocate next turn sequence and persist transcript turn."""
    clean_text = (text or "").strip()
    if not clean_text:
        return
    with state_lock:
        dedupe_key = f"{speaker}:{clean_text}"
        if bridge_state.get("last_transcript_key") == dedupe_key:
            return
        turn_sequence = bridge_state.get("next_transcript_turn_sequence", 1)
        bridge_state["next_transcript_turn_sequence"] = turn_sequence + 1
        bridge_state["last_transcript_key"] = dedupe_key
    _persist_transcript_turn(interaction_id, speaker, clean_text, turn_sequence, raw_payload)
    log_fn(f"Transcript captured [{speaker} #{turn_sequence}]: {clean_text}")


def _handle_openai_responses(
    openai_ws,
    twilio_ws,
    stream_sid: str,
    call_sid: str,
    interaction_id: Optional[str],
    metrics_service,
    bridge_state,
    state_lock,
    job_id: Optional[str] = None,
):
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

    def debug_event(event_name: str, metadata: Optional[dict] = None):
        _append_job_debug_event(job_id, event_name, metadata or {})

    log(f"OpenAI response handler started for call {call_sid}")
    debug_event("openai_response_handler_started", {"call_sid": call_sid})
    first_audio_recorded = False
    message_count = 0
    audio_chunks_sent = 0
    exit_reason = "unknown"
    greeting_completed = False
    use_elevenlabs_output = False
    with state_lock:
        use_elevenlabs_output = bool(bridge_state.get("use_elevenlabs_output"))

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
                    if event_type in ("error", "response.done", "session.updated", "response.created", "response.output_audio.done", "response.output_item.done", "response.output_audio_transcript.done"):
                        log(f"  Full data: {json.dumps(data)[:800]}")

                if event_type == "response.output_text.delta" or event_type == "response.text.delta":
                    delta_text = data.get("delta")
                    if delta_text:
                        with state_lock:
                            parts = bridge_state.get("assistant_text_parts")
                            if isinstance(parts, list):
                                parts.append(str(delta_text))
                elif event_type == "response.output_text.done" or event_type == "response.text.done":
                    done_text = data.get("text") or data.get("transcript") or data.get("delta")
                    if done_text:
                        with state_lock:
                            parts = bridge_state.get("assistant_text_parts")
                            if isinstance(parts, list):
                                parts.append(str(done_text))

                if event_type == "response.output_audio.delta":
                    if use_elevenlabs_output:
                        continue
                    # Streaming audio from OpenAI
                    audio_b64 = data.get("delta", "")
                    if audio_b64:
                        should_pause_turn_detection = False
                        with state_lock:
                            if not bridge_state.get("openai_turn_detection_muted"):
                                bridge_state["openai_turn_detection_muted"] = True
                                should_pause_turn_detection = True
                        if should_pause_turn_detection:
                            try:
                                _set_turn_detection_mode(
                                    openai_ws,
                                    vad_config=bridge_state.get("vad_config"),
                                    create_response=False,
                                    interrupt_response=False,
                                )
                                _clear_input_audio_buffer(openai_ws)
                                log("Paused OpenAI auto turn detection during assistant playback")
                                debug_event("turn_detection_paused", {"source": "openai_audio_delta"})
                            except Exception as e:
                                log(f"Failed to pause turn detection: {type(e).__name__}: {e}")

                        with state_lock:
                            bridge_state["assistant_playback_active"] = True
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
                    if use_elevenlabs_output:
                        continue
                    # Response complete
                    metrics_service.record_response_complete(call_sid)
                    log(f"Response audio complete, sent {audio_chunks_sent} audio chunks total")
                    first_audio_recorded = False  # Reset for next turn
                    should_resume_turn_detection = False
                    with state_lock:
                        bridge_state["last_assistant_audio_done_ms"] = time.monotonic() * 1000.0
                        bridge_state["assistant_playback_active"] = False
                        bridge_state["assistant_playback_block_until_ms"] = (
                            time.monotonic() * 1000.0
                            + float(bridge_state.get("playback_input_cooldown_ms", 0))
                        )
                        dropped_frames = int(bridge_state.get("playback_dropped_frames", 0))
                        bridge_state["playback_dropped_frames"] = 0
                        if bridge_state.get("openai_turn_detection_muted"):
                            bridge_state["openai_turn_detection_muted"] = False
                            should_resume_turn_detection = True
                    debug_event(
                        "assistant_playback_window_closed",
                        {
                            "source": "openai_audio_done",
                            "dropped_twilio_frames": dropped_frames,
                            "cooldown_ms": int(bridge_state.get("playback_input_cooldown_ms", 0)),
                        },
                    )
                    if should_resume_turn_detection:
                        try:
                            _set_turn_detection_mode(
                                openai_ws,
                                vad_config=bridge_state.get("vad_config"),
                                create_response=True,
                                interrupt_response=True,
                            )
                            log("Resumed OpenAI auto turn detection after assistant playback")
                            debug_event("turn_detection_resumed", {"source": "openai_audio_done"})
                        except Exception as e:
                            log(f"Failed to resume turn detection: {type(e).__name__}: {e}")

                elif event_type == "input_audio_buffer.speech_stopped":
                    # User stopped speaking - record timing
                    metrics_service.record_user_speech_end(call_sid)
                    log("User stopped speaking")
                    with state_lock:
                        bridge_state["saw_openai_speech_event"] = True
                    debug_event("input_audio_speech_stopped")

                elif event_type == "input_audio_buffer.speech_started":
                    log("User started speaking")
                    with state_lock:
                        bridge_state["saw_openai_speech_event"] = True
                    debug_event("input_audio_speech_started")

                elif event_type == "input_audio_buffer.committed":
                    log("Input audio buffer committed")
                    debug_event("input_audio_buffer_committed")

                elif event_type == "input_audio_buffer.timeout_triggered":
                    log("Input audio buffer timeout triggered")
                    debug_event("input_audio_buffer_timeout_triggered")

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
                    now_ms = time.monotonic() * 1000.0
                    with state_lock:
                        low_signal_filter_enabled = bool(
                            bridge_state.get("low_signal_filter_enabled", True)
                        )
                        low_signal_min_chars = int(bridge_state.get("low_signal_min_chars", 4))
                        low_signal_allowed_short_replies = set(
                            bridge_state.get("low_signal_allowed_short_replies")
                            or {"yes", "no", "ok", "sure"}
                        )
                        overlap_guard_ms = int(bridge_state.get("overlap_guard_ms", 600))
                        last_assistant_audio_done_ms = float(
                            bridge_state.get("last_assistant_audio_done_ms", 0.0)
                        )

                    # Guard: ignore overlap speech captured immediately after assistant playback ends.
                    # This prevents accidental talk-over from auto-triggering the next full response turn.
                    elapsed_since_assistant_done_ms = (
                        now_ms - last_assistant_audio_done_ms
                        if last_assistant_audio_done_ms > 0
                        else 999999.0
                    )
                    if elapsed_since_assistant_done_ms < float(overlap_guard_ms):
                        log(
                            "Ignoring overlap transcript captured too soon after assistant audio: "
                            f"{elapsed_since_assistant_done_ms:.0f}ms < {overlap_guard_ms}ms"
                        )
                        debug_event(
                            "ignored_overlap_transcript",
                            {
                                "text": (transcript or "")[:80],
                                "elapsed_ms": int(elapsed_since_assistant_done_ms),
                                "guard_ms": int(overlap_guard_ms),
                            },
                        )
                        try:
                            openai_ws.send(json.dumps({"type": "response.cancel"}))
                            openai_ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
                        except Exception:
                            pass
                        with state_lock:
                            bridge_state["awaiting_response"] = False
                        continue

                    if low_signal_filter_enabled and _is_low_signal_user_transcript(
                        transcript,
                        min_chars=low_signal_min_chars,
                        allowed_short_replies=low_signal_allowed_short_replies,
                    ):
                        log(f"Ignoring low-signal user transcript: {transcript!r}")
                        debug_event("ignored_low_signal_transcript", {"text": (transcript or "")[:80]})
                        # Do not cancel for empty transcript artifacts; cancelling can truncate active assistant output.
                        if (transcript or "").strip():
                            try:
                                openai_ws.send(json.dumps({"type": "response.cancel"}))
                                debug_event("response_cancel_sent_for_low_signal")
                            except Exception as cancel_err:
                                log(
                                    "Failed to cancel response after low-signal transcript: "
                                    f"{type(cancel_err).__name__}: {cancel_err}"
                                )
                        with state_lock:
                            bridge_state["awaiting_response"] = False
                        continue
                    _store_transcript_turn(
                        interaction_id=interaction_id,
                        speaker="user",
                        text=transcript,
                        raw_payload=data,
                        bridge_state=bridge_state,
                        state_lock=state_lock,
                        log_fn=log,
                    )

                elif event_type == "response.output_audio_transcript.done":
                    if use_elevenlabs_output:
                        continue
                    # Final assistant transcript for this response.
                    transcript = data.get("transcript", "")
                    _store_transcript_turn(
                        interaction_id=interaction_id,
                        speaker="assistant",
                        text=transcript,
                        raw_payload=data,
                        bridge_state=bridge_state,
                        state_lock=state_lock,
                        log_fn=log,
                    )

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
                    debug_event("openai_response_done")
                    response = data.get("response", {}) or {}
                    fallback_assistant_text = ""
                    for output_item in response.get("output", []) or []:
                        _handle_end_call_signal(output_item, call_sid, bridge_state, state_lock, log)
                        # Fallback transcript extraction from response payload.
                        content_items = output_item.get("content", []) if isinstance(output_item, dict) else []
                        for content_item in content_items:
                            if not isinstance(content_item, dict):
                                continue
                            transcript_text = (
                                content_item.get("transcript")
                                or content_item.get("text")
                            )
                            if transcript_text and not fallback_assistant_text:
                                fallback_assistant_text = str(transcript_text).strip()
                                if not use_elevenlabs_output:
                                    _store_transcript_turn(
                                        interaction_id=interaction_id,
                                        speaker="assistant",
                                        text=fallback_assistant_text,
                                        raw_payload=data,
                                        bridge_state=bridge_state,
                                        state_lock=state_lock,
                                        log_fn=log,
                                    )
                                break

                    if use_elevenlabs_output:
                        with state_lock:
                            text_parts = list(bridge_state.get("assistant_text_parts") or [])
                            bridge_state["assistant_text_parts"] = []
                        assistant_text = _extract_assistant_text(data, text_parts) or fallback_assistant_text
                        if assistant_text:
                            _store_transcript_turn(
                                interaction_id=interaction_id,
                                speaker="assistant",
                                text=assistant_text,
                                raw_payload=data,
                                bridge_state=bridge_state,
                                state_lock=state_lock,
                                log_fn=log,
                            )
                        should_pause_turn_detection = False
                        with state_lock:
                            if not bridge_state.get("openai_turn_detection_muted"):
                                bridge_state["openai_turn_detection_muted"] = True
                                should_pause_turn_detection = True
                        if should_pause_turn_detection:
                            try:
                                _set_turn_detection_mode(
                                    openai_ws,
                                    vad_config=bridge_state.get("vad_config"),
                                    create_response=False,
                                    interrupt_response=False,
                                )
                                _clear_input_audio_buffer(openai_ws)
                                log("Paused OpenAI auto turn detection during ElevenLabs playback")
                                debug_event("turn_detection_paused", {"source": "elevenlabs_playback"})
                            except Exception as e:
                                log(f"Failed to pause turn detection: {type(e).__name__}: {e}")
                        with state_lock:
                            bridge_state["assistant_playback_active"] = True
                        ok = _stream_text_via_elevenlabs_to_twilio(
                            text=assistant_text,
                            twilio_ws=twilio_ws,
                            stream_sid=stream_sid,
                            call_sid=call_sid,
                            metrics_service=metrics_service,
                            log_fn=log,
                        )
                        should_resume_turn_detection = False
                        with state_lock:
                            bridge_state["last_assistant_audio_done_ms"] = time.monotonic() * 1000.0
                            bridge_state["assistant_playback_active"] = False
                            bridge_state["assistant_playback_block_until_ms"] = (
                                time.monotonic() * 1000.0
                                + float(bridge_state.get("playback_input_cooldown_ms", 0))
                            )
                            dropped_frames = int(bridge_state.get("playback_dropped_frames", 0))
                            bridge_state["playback_dropped_frames"] = 0
                            if bridge_state.get("openai_turn_detection_muted"):
                                bridge_state["openai_turn_detection_muted"] = False
                                should_resume_turn_detection = True
                        debug_event(
                            "assistant_playback_window_closed",
                            {
                                "source": "elevenlabs_playback",
                                "dropped_twilio_frames": dropped_frames,
                                "cooldown_ms": int(bridge_state.get("playback_input_cooldown_ms", 0)),
                            },
                        )
                        if should_resume_turn_detection:
                            try:
                                _set_turn_detection_mode(
                                    openai_ws,
                                    vad_config=bridge_state.get("vad_config"),
                                    create_response=True,
                                    interrupt_response=True,
                                )
                                log("Resumed OpenAI auto turn detection after ElevenLabs playback")
                                debug_event("turn_detection_resumed", {"source": "elevenlabs_playback"})
                            except Exception as e:
                                log(f"Failed to resume turn detection: {type(e).__name__}: {e}")
                        if not ok:
                            switched = _fallback_to_openai_audio_mode(
                                openai_ws=openai_ws,
                                assistant_text=assistant_text,
                                bridge_state=bridge_state,
                                state_lock=state_lock,
                                log_fn=log,
                            )
                            if switched:
                                use_elevenlabs_output = False
                                continue
                            with state_lock:
                                bridge_state["end_call_requested"] = True
                            _request_call_hangup_with_message(
                                call_sid,
                                "Sorry, we are having a temporary voice issue. Please try again shortly. Goodbye.",
                            )
                            exit_reason = "elevenlabs_stream_failure"
                            break

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
                            _enable_post_greeting_barge_in(openai_ws, bridge_state.get("vad_config"))
                            log("Post-greeting barge-in mode enabled")
                        except Exception as e:
                            log(f"Failed to enable post-greeting barge-in mode: {type(e).__name__}: {e}")

                elif event_type == "response.created":
                    debug_event("openai_response_created")
                    with state_lock:
                        bridge_state["awaiting_response"] = True
                        bridge_state["assistant_text_parts"] = []

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


