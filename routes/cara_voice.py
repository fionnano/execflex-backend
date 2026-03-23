"""
Cara voice session management.

POST /voice-session/cara — create a session with a system prompt.
Returns session_id + WebSocket URL for the browser to connect to.

The system prompt is stored server-side in a TTL dict keyed by session_id.
The WebSocket URL contains only the session_id — no prompt in the URL.
"""
import os
import uuid
import time
import threading
from flask import Blueprint, request, jsonify

cara_bp = Blueprint("cara", __name__)

# Allowed origins for Cara voice sessions (browser-direct calls)
_CARA_ALLOWED_ORIGINS = {
    "https://ainm.ai",
    "https://www.ainm.ai",
    "https://execflex.ai",
    "http://localhost:5173",
    "http://localhost:3000",
}

# ── In-memory session store ───────────────────────────────────────────────────
# Maps session_id → { "prompt": str, "expires": float }
# TTL of 5 minutes — plenty of time for the browser to open the WebSocket.
_SESSION_TTL = 300  # seconds
_sessions: dict = {}
_sessions_lock = threading.Lock()


def _store_session(session_id: str, system_prompt: str) -> None:
    with _sessions_lock:
        _sessions[session_id] = {
            "prompt": system_prompt,
            "expires": time.time() + _SESSION_TTL,
        }


def get_session_prompt(session_id: str) -> str | None:
    """Return the system prompt for a session, or None if expired/missing."""
    with _sessions_lock:
        entry = _sessions.get(session_id)
        if not entry:
            return None
        if time.time() > entry["expires"]:
            del _sessions[session_id]
            return None
        # Remove after first use — no replay needed
        del _sessions[session_id]
        return entry["prompt"]


def _cleanup_expired() -> None:
    """Periodically remove expired sessions to avoid memory leaks."""
    while True:
        time.sleep(120)
        now = time.time()
        with _sessions_lock:
            expired = [k for k, v in _sessions.items() if now > v["expires"]]
            for k in expired:
                del _sessions[k]
        if expired:
            print(f"[Cara] Cleaned up {len(expired)} expired sessions", flush=True)


# Start background cleanup thread
_cleanup_thread = threading.Thread(target=_cleanup_expired, daemon=True)
_cleanup_thread.start()


# ── REST endpoint ─────────────────────────────────────────────────────────────

@cara_bp.route("/voice-session/cara", methods=["POST"])
def create_voice_session():
    """
    POST /voice-session/cara

    Auth: accepts Supabase JWT, X-Service-Key, or requests from allowed
    origins (ainm.ai, execflex.ai, localhost). The system_prompt body
    acts as implicit auth since only Ainm generates it with RAG context.

    Body (JSON):
        system_prompt   str  — Full system prompt for Cara (built by ainm.ai with RAG context)

    Returns:
        201 { session_id, ws_url }
    """
    # Allow if: valid JWT/service key, OR request from allowed origin
    from utils.auth_helpers import get_authenticated_user_id
    user_id, _ = get_authenticated_user_id()
    if not user_id:
        origin = (request.headers.get("Origin") or "").rstrip("/")
        if origin not in _CARA_ALLOWED_ORIGINS:
            print(f"[Cara] Rejected: no auth and origin={origin!r} not allowed", flush=True)
            return jsonify({"error": "Authentication required"}), 401
        print(f"[Cara] Allowed via origin: {origin}", flush=True)

    data = request.get_json(force=True) or {}
    system_prompt = (data.get("system_prompt") or "").strip()
    if not system_prompt:
        return jsonify({"error": "system_prompt is required"}), 400

    session_id = str(uuid.uuid4())

    # Store system prompt server-side — keeps the WebSocket URL short and clean
    _store_session(session_id, system_prompt)

    base_url = os.getenv("EXECFLEX_BASE_URL", "wss://execflex-backend-1.onrender.com")
    base_url = base_url.rstrip("/")
    if base_url.startswith("https://"):
        base_url = "wss://" + base_url[8:]
    elif base_url.startswith("http://"):
        base_url = "ws://" + base_url[7:]

    ws_url = f"{base_url}/voice/cara/ws/{session_id}"

    print(f"[Cara] Created session {session_id}, prompt len={len(system_prompt)}", flush=True)

    return jsonify({
        "session_id": session_id,
        "ws_url": ws_url,
    }), 201
