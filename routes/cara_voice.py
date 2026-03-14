"""
Cara voice session management.

POST /voice-session/cara — create a session with a system prompt.
Returns session_id + WebSocket URL for the browser to connect to.
Sessions are single-use and expire after 10 minutes.
"""
import os
import uuid
import time
import threading
from typing import Dict, Any, Optional

from flask import Blueprint, request, jsonify

cara_bp = Blueprint("cara", __name__)

# ── In-memory session store ───────────────────────────────────────────────────
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_SESSIONS_LOCK = threading.Lock()
_SESSION_TTL = 600  # 10 minutes


def _cleanup_old_sessions():
    now = time.time()
    with _SESSIONS_LOCK:
        stale = [sid for sid, s in _SESSIONS.items() if now - s["created_at"] > _SESSION_TTL]
        for sid in stale:
            del _SESSIONS[sid]


def create_cara_session(system_prompt: str) -> str:
    _cleanup_old_sessions()
    session_id = str(uuid.uuid4())
    with _SESSIONS_LOCK:
        _SESSIONS[session_id] = {
            "system_prompt": system_prompt,
            "created_at": time.time(),
        }
    return session_id


def get_cara_session(session_id: str) -> Optional[Dict[str, Any]]:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(session_id)


def delete_cara_session(session_id: str):
    with _SESSIONS_LOCK:
        _SESSIONS.pop(session_id, None)


# ── REST endpoint ─────────────────────────────────────────────────────────────

@cara_bp.route("/voice-session/cara", methods=["POST"])
def create_voice_session():
    """
    POST /voice-session/cara

    Body (JSON):
        system_prompt   str  — Full system prompt for Cara (built by ainm.ai with RAG context)

    Returns:
        201 { session_id, ws_url }
    """
    data = request.get_json(force=True) or {}
    system_prompt = (data.get("system_prompt") or "").strip()
    if not system_prompt:
        return jsonify({"error": "system_prompt is required"}), 400

    session_id = create_cara_session(system_prompt)

    # Build WebSocket URL — use EXECFLEX_BASE_URL env var or default
    base_url = os.getenv("EXECFLEX_BASE_URL", "wss://execflex-backend-1.onrender.com")
    # Ensure no trailing slash and correct scheme
    base_url = base_url.rstrip("/")
    if base_url.startswith("https://"):
        base_url = "wss://" + base_url[8:]
    elif base_url.startswith("http://"):
        base_url = "ws://" + base_url[7:]

    ws_url = f"{base_url}/voice/cara/ws/{session_id}"

    print(f"[Cara] Created session {session_id}", flush=True)

    return jsonify({
        "session_id": session_id,
        "ws_url": ws_url,
    }), 201
