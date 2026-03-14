"""
Cara WebSocket bridge: browser PCM16 24kHz <-> OpenAI Realtime API.

Browser protocol (JSON messages):
  Browser → Server:
    {"type": "audio", "data": "<base64 PCM16 24kHz>"}  — user audio chunk
    {"type": "end"}                                      — end the call

  Server → Browser:
    {"type": "session_started"}
    {"type": "audio", "data": "<base64 PCM16 24kHz>"}   — assistant audio
    {"type": "transcript_delta", "role": "assistant", "text": "..."}
    {"type": "transcript_done",  "role": "assistant", "text": "..."}
    {"type": "transcript",       "role": "user",      "text": "..."}
    {"type": "speech_started"}   — user started speaking (VAD)
    {"type": "speech_stopped"}   — user stopped speaking
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

from flask import request as flask_request
from flask_sock import Sock
from simple_websocket import Server as SimpleWebSocket

from config.app_config import OPENAI_API_KEY
from routes.cara_voice import decode_system_prompt

_OPENAI_REALTIME_VOICE = "shimmer"  # Warm female voice
_OPENAI_REALTIME_MODEL_DEFAULT = "gpt-4o-realtime-preview-2024-12-17"


def init_cara_websocket(sock: Sock):
    """Register the Cara WebSocket route with the Flask-Sock instance."""

    @sock.route("/voice/cara/ws/<session_id>")
    def cara_ws_handler(ws: SimpleWebSocket, session_id: str):
        """Handle a Cara real-time voice session."""
        import websocket as ws_client

        print(f"[Cara] WebSocket connection opened for session {session_id}", flush=True)

        # ── Extract system prompt from URL query param (stateless — works across instances) ──
        sp_encoded = flask_request.args.get("sp", "")
        if not sp_encoded:
            print(f"[Cara] No system prompt in URL for session {session_id}", flush=True)
            _safe_send(ws, {"type": "error", "message": "Session not found or expired"})
            return
        try:
            system_prompt = decode_system_prompt(sp_encoded)
            print(f"[Cara] Decoded system_prompt len={len(system_prompt)} for session {session_id}", flush=True)
        except Exception as e:
            print(f"[Cara] Failed to decode system_prompt: {e}", flush=True)
            _safe_send(ws, {"type": "error", "message": "Invalid session data"})
            return

        if not OPENAI_API_KEY:
            _safe_send(ws, {"type": "error", "message": "OpenAI not configured on server"})
            return

        # ── State shared between threads ─────────────────────────────────────
        stop_event = threading.Event()
        transcript_turns: List[Dict[str, str]] = []
        openai_ws_ref: List[Optional[Any]] = [None]
        # Lock for thread-safe sends to OpenAI (main loop + background thread both send)
        openai_send_lock = threading.Lock()

        # ── Connect to OpenAI Realtime API ───────────────────────────────────
        model = os.getenv("OPENAI_REALTIME_MODEL", _OPENAI_REALTIME_MODEL_DEFAULT)
        url = f"wss://api.openai.com/v1/realtime?model={model}"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1",
        }

        try:
            openai_ws = ws_client.create_connection(
                url,
                header=[f"{k}: {v}" for k, v in headers.items()],
                sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                timeout=20,
                skip_utf8_validation=True,
            )
            openai_ws.settimeout(None)
            openai_ws_ref[0] = openai_ws
            print(f"[Cara] Connected to OpenAI Realtime for session {session_id}", flush=True)
        except Exception as e:
            print(f"[Cara] FAILED to connect to OpenAI Realtime: {type(e).__name__}: {e}", flush=True)
            _safe_send(ws, {"type": "error", "message": f"Failed to connect to AI backend: {type(e).__name__}"})
            return

        # ── Wait for session.created ─────────────────────────────────────────
        try:
            for _ in range(15):
                raw = openai_ws.recv()
                if not raw:
                    break
                msg = json.loads(raw)
                if msg.get("type") == "session.created":
                    print(f"[Cara] OpenAI session created", flush=True)
                    break
                if msg.get("type") == "error":
                    print(f"[Cara] OpenAI session error: {msg}", flush=True)
                    _safe_send(ws, {"type": "error", "message": "OpenAI session error"})
                    openai_ws.close()
                    return
        except Exception as e:
            print(f"[Cara] Error waiting for session.created: {e}", flush=True)

        # ── Configure session ─────────────────────────────────────────────────
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": system_prompt,
                "voice": _OPENAI_REALTIME_VOICE,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.8,
                    "prefix_padding_ms": 500,
                    "silence_duration_ms": 1500,
                    "create_response": True,
                },
                "temperature": 0.8,
                "max_response_output_tokens": 800,
            },
        }
        try:
            openai_ws.send(json.dumps(session_config))
        except Exception as e:
            print(f"[Cara] Failed to send session config: {e}", flush=True)

        # ── Send greeting ─────────────────────────────────────────────────────
        # No `instructions` override — Cara follows her system prompt directly.
        # This lets training sessions open differently from HR advisory sessions.
        greeting_request = {
            "type": "response.create",
            "response": {
                "modalities": ["audio", "text"],
            },
        }
        try:
            openai_ws.send(json.dumps(greeting_request))
        except Exception as e:
            print(f"[Cara] Failed to send greeting: {e}", flush=True)

        # ── OpenAI → Browser thread ───────────────────────────────────────────
        current_assistant_text: List[str] = []
        response_active: List[bool] = [False]  # True while Cara is generating audio

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
                            # On first audio chunk of each response, clear the input
                            # buffer so any residual user audio can't trigger the VAD
                            # and interrupt Cara mid-sentence.
                            if not response_active[0]:
                                response_active[0] = True
                                try:
                                    with openai_send_lock:
                                        openai_ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
                                except Exception:
                                    pass
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

                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        text = (data.get("transcript") or "").strip()
                        if text:
                            transcript_turns.append({"role": "user", "text": text})
                            if not _safe_send(ws, {"type": "transcript", "role": "user", "text": text}):
                                break

                    elif event_type == "input_audio_buffer.speech_started":
                        _safe_send(ws, {"type": "speech_started"})

                    elif event_type == "input_audio_buffer.speech_stopped":
                        _safe_send(ws, {"type": "speech_stopped"})

                    elif event_type == "error":
                        err = data.get("error", {})
                        print(f"[Cara] OpenAI error event: {err}", flush=True)
                        _safe_send(ws, {"type": "error", "message": err.get("message", "OpenAI error")})

                except Exception as recv_err:
                    if not stop_event.is_set():
                        print(f"[Cara] OpenAI recv error: {recv_err}", flush=True)
                    break

            stop_event.set()
            print(f"[Cara] OpenAI→browser thread exiting", flush=True)

        response_thread = threading.Thread(target=openai_to_browser, daemon=True)
        response_thread.start()

        # ── Notify browser: session ready ─────────────────────────────────────
        if not _safe_send(ws, {"type": "session_started"}):
            stop_event.set()
            _close_openai(openai_ws)
            return

        # ── Browser → OpenAI main loop ────────────────────────────────────────
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
                            print(f"[Cara] Error forwarding audio to OpenAI: {e}", flush=True)

                elif msg_type == "end":
                    print(f"[Cara] Browser sent end signal", flush=True)
                    break

        except Exception as e:
            print(f"[Cara] Main loop error: {e}", flush=True)

        finally:
            stop_event.set()
            _close_openai(openai_ws_ref[0])
            response_thread.join(timeout=3)

            # Send final transcript to browser
            _safe_send(ws, {
                "type": "call_ended",
                "transcript_turns": transcript_turns,
            })

        print(f"[Cara] Session {session_id} ended. Turns: {len(transcript_turns)}", flush=True)


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
