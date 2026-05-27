"""
Cara voice session management.

POST /voice-session/cara — create a session with a system prompt.
Returns session_id + WebSocket URL for the browser to connect to.

The system prompt is stored server-side in a TTL dict keyed by session_id.
The WebSocket URL contains only the session_id — no prompt in the URL.

Stateless URL-encoding was tried (commit 3019f26) but reverted: RAG-enriched
prompts exceed Render's nginx URL length limit. With --workers 1 in the
Procfile, the in-memory store is reliable (single process handles both
POST and WebSocket).
"""
import os
import uuid
import time
import threading
from flask import Blueprint, request, jsonify

from config.app_config import OPENAI_API_KEY

cara_bp = Blueprint("cara", __name__)

_CARA_ALLOWED_ORIGINS = {
    "https://ainm.ai",
    "https://www.ainm.ai",
    "https://execflex.ai",
    "http://localhost:5173",
    "http://localhost:3000",
}

# ── In-memory session store ───────────────────────────────────────────────────
_SESSION_TTL = 300  # 5 minutes
_sessions: dict = {}
_sessions_lock = threading.Lock()

# Counters for health probe
_session_stats = {"created": 0, "retrieved": 0, "expired": 0, "not_found": 0}


def _log(session_id: str | None, event: str, **kv) -> None:
    sid = session_id[:8] if session_id else "------"
    extras = " ".join(f"{k}={v}" for k, v in kv.items()) if kv else ""
    print(f"[Cara:{sid}] {event} {extras}".rstrip(), flush=True)


def _store_session(session_id: str, system_prompt: str) -> None:
    with _sessions_lock:
        _sessions[session_id] = {
            "prompt": system_prompt,
            "expires": time.time() + _SESSION_TTL,
        }
        _session_stats["created"] += 1


def get_session_prompt(session_id: str) -> str | None:
    """Return the system prompt for a session, or None if expired/missing."""
    with _sessions_lock:
        entry = _sessions.get(session_id)
        if not entry:
            _session_stats["not_found"] += 1
            return None
        if time.time() > entry["expires"]:
            del _sessions[session_id]
            _session_stats["expired"] += 1
            return None
        prompt = entry["prompt"]
        del _sessions[session_id]
        _session_stats["retrieved"] += 1
        return prompt


def _cleanup_expired() -> None:
    while True:
        time.sleep(120)
        now = time.time()
        with _sessions_lock:
            expired = [k for k, v in _sessions.items() if now > v["expires"]]
            for k in expired:
                del _sessions[k]
                _session_stats["expired"] += 1
        if expired:
            _log(None, "cleanup", removed=len(expired))


_cleanup_thread = threading.Thread(target=_cleanup_expired, daemon=True)
_cleanup_thread.start()


# ── Health probe ──────────────────────────────────────────────────────────────

@cara_bp.route("/health/voice", methods=["GET"])
def voice_health():
    """
    GET /health/voice — lightweight probe that checks:
      1. OpenAI API key is present
      2. Session store is functional (create + retrieve round-trip)
    """
    checks = {}

    checks["openai_key"] = bool(OPENAI_API_KEY)

    probe_id = f"health-{uuid.uuid4()}"
    _store_session(probe_id, "__probe__")
    retrieved = get_session_prompt(probe_id)
    checks["session_store"] = (retrieved == "__probe__")

    with _sessions_lock:
        checks["active_sessions"] = len(_sessions)
    checks["stats"] = dict(_session_stats)

    healthy = checks["openai_key"] and checks["session_store"]
    return jsonify({"status": "ok" if healthy else "degraded", **checks}), (200 if healthy else 503)


# ── REST endpoint ─────────────────────────────────────────────────────────────

@cara_bp.route("/voice-session/cara", methods=["POST"])
def create_voice_session():
    """
    POST /voice-session/cara

    Auth: accepts Supabase JWT, X-Service-Key, or requests from allowed
    origins (ainm.ai, execflex.ai, localhost).

    Body (JSON):
        system_prompt   str  — Full system prompt for Cara

    Returns:
        201 { session_id, ws_url }
    """
    from utils.auth_helpers import get_authenticated_user_id
    user_id, _ = get_authenticated_user_id()
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if not user_id:
        if origin not in _CARA_ALLOWED_ORIGINS:
            _log(None, "REJECTED", origin=repr(origin), reason="no_auth_no_origin")
            return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(force=True) or {}
    system_prompt = (data.get("system_prompt") or "").strip()
    if not system_prompt:
        return jsonify({"error": "system_prompt is required"}), 400

    session_id = str(uuid.uuid4())
    _store_session(session_id, system_prompt)

    base_url = os.getenv("EXECFLEX_BASE_URL", "wss://execflex-backend-1.onrender.com")
    base_url = base_url.rstrip("/")
    if base_url.startswith("https://"):
        base_url = "wss://" + base_url[8:]
    elif base_url.startswith("http://"):
        base_url = "ws://" + base_url[7:]

    ws_url = f"{base_url}/voice/cara/ws/{session_id}"

    _log(session_id, "SESSION_CREATED", prompt_len=len(system_prompt),
         auth="jwt" if user_id else f"origin:{origin}")

    return jsonify({
        "session_id": session_id,
        "ws_url": ws_url,
    }), 201
