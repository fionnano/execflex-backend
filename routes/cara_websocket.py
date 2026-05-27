"""
Cara WebSocket bridge: browser PCM16 24kHz <-> OpenAI Realtime API.

Browser protocol (JSON messages):
  Browser -> Server:
    {"type": "audio", "data": "<base64 PCM16 24kHz>"}
    {"type": "end"}

  Server -> Browser:
    {"type": "session_started"}
    {"type": "audio", "data": "<base64 PCM16 24kHz>"}
    {"type": "transcript_delta", "role": "assistant", "text": "..."}
    {"type": "transcript_done",  "role": "assistant", "text": "..."}
    {"type": "transcript",       "role": "user",      "text": "..."}
    {"type": "speech_started"}
    {"type": "speech_stopped"}
    {"type": "call_ended",       "transcript_turns": [...]}
    {"type": "error",            "message": "..."}

Initialized from server.py via init_cara_websocket(sock).
"""
import json
import ssl
import threading
import time
import os
from typing import Optional, List, Dict, Any

from flask_sock import Sock
from simple_websocket import Server as SimpleWebSocket

from config.app_config import OPENAI_API_KEY
from routes.cara_voice import get_session_prompt

_OPENAI_REALTIME_VOICE = "shimmer"
_OPENAI_REALTIME_MODEL_DEFAULT = "gpt-realtime"


def _log(session_id: str, event: str, **kv) -> None:
    sid = session_id[:8] if session_id else "------"
    extras = " ".join(f"{k}={v}" for k, v in kv.items()) if kv else ""
    print(f"[Cara:{sid}] {event} {extras}".rstrip(), flush=True)


def init_cara_websocket(sock: Sock):
    """Register the Cara WebSocket route with the Flask-Sock instance."""

    @sock.route("/voice/cara/ws/<session_id>")
    def cara_ws_handler(ws: SimpleWebSocket, session_id: str):
        """Handle a Cara real-time voice session."""
        import websocket as ws_client

        t0 = time.monotonic()
        _log(session_id, "WS_OPEN")

        # ── Look up system prompt from server-side session store ──────────────
        system_prompt = get_session_prompt(session_id)
        if not system_prompt:
            _log(session_id, "SESSION_NOT_FOUND")
            _safe_send(ws, {"type": "error", "message": "Session not found or expired"})
            return

        _CARA_PREAMBLE = (
            "IMPORTANT: Always respond in English, regardless of the language "
            "the user speaks or the language of any provided context.\n\n"
            "STEP 1 (your very first message — say ONLY this): "
            "\"Hi, I'm Cara, your HR assistant. How can I help you today?\"\n"
            "Do not add anything else to your first message. Wait for the user to respond.\n\n"
        )
        system_prompt = _CARA_PREAMBLE + system_prompt
        _log(session_id, "PROMPT_LOADED", prompt_len=len(system_prompt))

        if not OPENAI_API_KEY:
            _log(session_id, "NO_OPENAI_KEY")
            _safe_send(ws, {"type": "error", "message": "OpenAI not configured on server"})
            return

        # ── State shared between threads ─────────────────────────────────────
        stop_event = threading.Event()
        transcript_turns: List[Dict[str, str]] = []
        openai_ws_ref: List[Optional[Any]] = [None]
        openai_send_lock = threading.Lock()

        # ── Connect to OpenAI Realtime API ───────────────────────────────────
        # Mirrors _connect_openai_sync() in voice_websocket.py exactly:
        # same URL, same headers, same retry logic, same keepalive.
        import socket as _socket

        model = os.getenv("OPENAI_REALTIME_MODEL", _OPENAI_REALTIME_MODEL_DEFAULT)
        url = f"wss://api.openai.com/v1/realtime?model={model}"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        }
        _log(session_id, "OPENAI_CONNECTING",
             url=url, model=model,
             env_model=repr(os.getenv("OPENAI_REALTIME_MODEL")))

        openai_ws = None
        try:
            # Retry loop — matches voice_websocket.py
            connect_err = None
            for attempt in range(1, 4):
                try:
                    openai_ws = ws_client.create_connection(
                        url,
                        header=[f"{k}: {v}" for k, v in headers.items()],
                        sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                        timeout=20,
                        skip_utf8_validation=True,
                        sockopt=[(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1)],
                    )
                    break
                except Exception as e:
                    connect_err = e
                    _log(session_id, "OPENAI_CONNECT_RETRY",
                         attempt=attempt, error=f"{type(e).__name__}: {e}")
                    if attempt >= 3:
                        raise
                    time.sleep(0.4 * attempt)

            if openai_ws is None:
                raise RuntimeError(f"connect failed after retries: {connect_err}")
            openai_ws.settimeout(None)
            openai_ws_ref[0] = openai_ws
            _log(session_id, "OPENAI_CONNECTED", model=model,
                 elapsed_ms=int((time.monotonic() - t0) * 1000))
        except Exception as e:
            _log(session_id, "OPENAI_CONNECT_FAILED", error=f"{type(e).__name__}: {e}")
            _safe_send(ws, {"type": "error", "message": f"Failed to connect to AI backend: {type(e).__name__}"})
            return

        # ── Wait for session.created ─────────────────────────────────────────
        saw_session_created = False
        for _ in range(20):
            try:
                raw = openai_ws.recv()
            except Exception as e:
                _log(session_id, "SESSION_CREATED_RECV_ERROR", error=str(e))
                break
            if not raw:
                continue
            msg = json.loads(raw)
            event_type = msg.get("type")
            if event_type == "session.created":
                _log(session_id, "SESSION_CREATED")
                saw_session_created = True
                break
            if event_type == "error":
                err = msg.get("error", {})
                _log(session_id, "SESSION_CREATE_ERROR",
                     code=err.get("code", "?"), message=err.get("message", str(msg)))
                _safe_send(ws, {"type": "error",
                                "message": f"OpenAI rejected session: {err.get('message', 'unknown')}"})
                openai_ws.close()
                return
        if not saw_session_created:
            _log(session_id, "SESSION_CREATED_TIMEOUT")
            _safe_send(ws, {"type": "error", "message": "OpenAI session creation timed out"})
            openai_ws.close()
            return

        # ── Configure session ─────────────────────────────────────────────────
        # Structure mirrors voice_websocket.py session_config exactly.
        # Only differences: audio/pcm (browser) instead of audio/pcmu (Twilio),
        # shimmer voice, and Cara-specific VAD thresholds.
        session_config = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": model,
                "instructions": system_prompt,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {"model": "gpt-4o-mini-transcribe"},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.8,
                            "prefix_padding_ms": 500,
                            "silence_duration_ms": 1500,
                            "create_response": True,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": _OPENAI_REALTIME_VOICE,
                    },
                },
            },
        }
        session_json = json.dumps(session_config)
        _log(session_id, "SESSION_UPDATE_SENT", config_len=len(session_json))
        try:
            openai_ws.send(session_json)
        except Exception as e:
            _log(session_id, "SESSION_UPDATE_SEND_FAILED", error=str(e))
            openai_ws.close()
            return

        # Wait for session.updated — matches voice_websocket.py pattern
        saw_session_updated = False
        for _ in range(30):
            try:
                raw = openai_ws.recv()
            except Exception as e:
                _log(session_id, "SESSION_UPDATE_RECV_ERROR", error=str(e))
                break
            if not raw:
                continue
            msg = json.loads(raw)
            event_type = msg.get("type")
            if event_type == "session.updated":
                _log(session_id, "SESSION_CONFIGURED")
                saw_session_updated = True
                break
            if event_type == "error":
                err = msg.get("error", {})
                _log(session_id, "SESSION_UPDATE_ERROR",
                     code=err.get("code", "?"),
                     message=err.get("message", json.dumps(msg)[:500]))
                break
        if not saw_session_updated:
            _log(session_id, "SESSION_UPDATE_FAILED")
            _safe_send(ws, {"type": "error", "message": "OpenAI session configuration failed"})
            openai_ws.close()
            return

        # ── Send greeting ─────────────────────────────────────────────────────
        greeting_request = {"type": "response.create"}
        try:
            openai_ws.send(json.dumps(greeting_request))
            _log(session_id, "GREETING_SENT",
                 setup_ms=int((time.monotonic() - t0) * 1000))
        except Exception as e:
            _log(session_id, "GREETING_SEND_FAILED", error=str(e))

        # ── OpenAI -> Browser thread ───────────────────────────────────────
        current_assistant_text: List[str] = []
        response_active: List[bool] = [False]
        audio_chunks_sent: List[int] = [0]

        def openai_to_browser():
            nonlocal current_assistant_text
            while not stop_event.is_set():
                try:
                    raw = openai_ws.recv()
                    if not raw:
                        break
                    data = json.loads(raw)
                    event_type = data.get("type", "")

                    if event_type == "response.audio.delta":
                        audio_b64 = data.get("delta", "")
                        if audio_b64:
                            if not response_active[0]:
                                response_active[0] = True
                                try:
                                    with openai_send_lock:
                                        openai_ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
                                except Exception:
                                    pass
                            audio_chunks_sent[0] += 1
                            if not _safe_send(ws, {"type": "audio", "data": audio_b64}):
                                break

                    elif event_type == "response.audio_transcript.delta":
                        delta = data.get("delta", "")
                        if delta:
                            current_assistant_text.append(delta)
                            _safe_send(ws, {"type": "transcript_delta", "role": "assistant", "text": delta})

                    elif event_type == "response.audio_transcript.done":
                        text = "".join(current_assistant_text).strip()
                        current_assistant_text = []
                        response_active[0] = False
                        if text:
                            transcript_turns.append({"role": "assistant", "text": text})
                            _safe_send(ws, {"type": "transcript_done", "role": "assistant", "text": text})
                            _log(session_id, "ASSISTANT_TURN", chars=len(text))

                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        text = (data.get("transcript") or "").strip()
                        if text:
                            transcript_turns.append({"role": "user", "text": text})
                            _log(session_id, "USER_TURN", chars=len(text))
                            if not _safe_send(ws, {"type": "transcript", "role": "user", "text": text}):
                                break

                    elif event_type == "input_audio_buffer.speech_started":
                        _safe_send(ws, {"type": "speech_started"})

                    elif event_type == "input_audio_buffer.speech_stopped":
                        _safe_send(ws, {"type": "speech_stopped"})

                    elif event_type == "error":
                        err = data.get("error", {})
                        _log(session_id, "OPENAI_RUNTIME_ERROR",
                             code=err.get("code", "?"), message=err.get("message", "?"))
                        _safe_send(ws, {"type": "error", "message": err.get("message", "OpenAI error")})

                except Exception as recv_err:
                    if not stop_event.is_set():
                        _log(session_id, "OPENAI_RECV_ERROR", error=str(recv_err))
                    break

            stop_event.set()

        response_thread = threading.Thread(target=openai_to_browser, daemon=True)
        response_thread.start()

        # ── Notify browser: session ready ─────────────────────────────────────
        if not _safe_send(ws, {"type": "session_started"}):
            stop_event.set()
            _close_openai(openai_ws)
            return

        _log(session_id, "SESSION_LIVE")

        # ── Browser -> OpenAI main loop ────────────────────────────────────────
        try:
            while not stop_event.is_set():
                try:
                    message = ws.receive(timeout=30)
                except Exception:
                    break

                if message is None:
                    break

                try:
                    data = json.loads(message)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = data.get("type")

                if msg_type == "audio":
                    audio_b64 = data.get("data", "")
                    if audio_b64 and openai_ws_ref[0]:
                        try:
                            with openai_send_lock:
                                openai_ws_ref[0].send(json.dumps({
                                    "type": "input_audio_buffer.append",
                                    "audio": audio_b64,
                                }))
                        except Exception as e:
                            _log(session_id, "AUDIO_FWD_ERROR", error=str(e))

                elif msg_type == "end":
                    _log(session_id, "BROWSER_END_SIGNAL")
                    break

        except Exception as e:
            _log(session_id, "MAIN_LOOP_ERROR", error=str(e))

        finally:
            stop_event.set()
            _close_openai(openai_ws_ref[0])
            response_thread.join(timeout=3)

            _safe_send(ws, {
                "type": "call_ended",
                "transcript_turns": transcript_turns,
            })

        elapsed = int((time.monotonic() - t0) * 1000)
        _log(session_id, "SESSION_ENDED", turns=len(transcript_turns),
             audio_chunks=audio_chunks_sent[0], duration_ms=elapsed)


def _safe_send(ws: SimpleWebSocket, data: dict) -> bool:
    """Send JSON to browser WebSocket. Returns False on failure."""
    try:
        ws.send(json.dumps(data))
        return True
    except Exception:
        return False


def _close_openai(openai_ws) -> None:
    """Safely close the OpenAI WebSocket."""
    if openai_ws:
        try:
            openai_ws.close()
        except Exception:
            pass
