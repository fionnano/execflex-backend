"""
Cara voice session management.

POST /voice-session/cara — create a session with a system prompt.
Returns session_id + WebSocket URL for the browser to connect to.

Sessions are stored on the filesystem (/tmp/cara_sessions/), not in
process memory. Render routes HTTP POST and WebSocket upgrade through
different process contexts even with --workers 1, so in-memory dicts
are not shared between them. The filesystem is shared within the
container and works regardless of process model.
"""
import os
import json
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

# ── Filesystem session store ──────────────────────────────────────────────────
_SESSION_TTL = 300  # 5 minutes
_SESSION_DIR = os.path.join("/tmp", "cara_sessions")
os.makedirs(_SESSION_DIR, exist_ok=True)

_session_stats = {"created": 0, "retrieved": 0, "expired": 0, "not_found": 0}


def _log(session_id: str | None, event: str, **kv) -> None:
    sid = session_id[:8] if session_id else "------"
    extras = " ".join(f"{k}={v}" for k, v in kv.items()) if kv else ""
    print(f"[Cara:{sid}] {event} {extras}".rstrip(), flush=True)


def _session_path(session_id: str) -> str:
    return os.path.join(_SESSION_DIR, session_id)


def _store_session(session_id: str, system_prompt: str) -> None:
    data = json.dumps({"prompt": system_prompt, "expires": time.time() + _SESSION_TTL})
    path = _session_path(session_id)
    with open(path, "w") as f:
        f.write(data)
    _session_stats["created"] += 1
    _log(session_id, "STORED_TO_DISK", path=path, size=len(data))


def get_session_prompt(session_id: str) -> str | None:
    """Return the system prompt for a session, or None if expired/missing."""
    path = _session_path(session_id)
    try:
        with open(path, "r") as f:
            raw = f.read()
    except FileNotFoundError:
        _session_stats["not_found"] += 1
        _log(session_id, "NOT_FOUND_ON_DISK", path=path)
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        _session_stats["not_found"] += 1
        _log(session_id, "CORRUPT_SESSION_FILE", path=path)
        _safe_unlink(path)
        return None

    if time.time() > data.get("expires", 0):
        _session_stats["expired"] += 1
        _safe_unlink(path)
        return None

    _safe_unlink(path)
    _session_stats["retrieved"] += 1
    return data.get("prompt")


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _cleanup_expired() -> None:
    while True:
        time.sleep(120)
        removed = 0
        try:
            now = time.time()
            for fname in os.listdir(_SESSION_DIR):
                path = os.path.join(_SESSION_DIR, fname)
                try:
                    with open(path, "r") as f:
                        data = json.loads(f.read())
                    if now > data.get("expires", 0):
                        _safe_unlink(path)
                        _session_stats["expired"] += 1
                        removed += 1
                except (json.JSONDecodeError, OSError):
                    _safe_unlink(path)
                    removed += 1
        except OSError:
            pass
        if removed:
            _log(None, "cleanup", removed=removed)


_cleanup_thread = threading.Thread(target=_cleanup_expired, daemon=True)
_cleanup_thread.start()


# ── Health probe ──────────────────────────────────────────────────────────────

@cara_bp.route("/health/voice", methods=["GET"])
def voice_health():
    """
    GET /health/voice — checks OpenAI key + session store round-trip.
    """
    checks = {}
    checks["openai_key"] = bool(OPENAI_API_KEY)
    checks["session_dir"] = os.path.isdir(_SESSION_DIR)

    probe_id = f"health-{uuid.uuid4()}"
    _store_session(probe_id, "__probe__")
    retrieved = get_session_prompt(probe_id)
    checks["session_store"] = (retrieved == "__probe__")

    try:
        checks["active_sessions"] = len(os.listdir(_SESSION_DIR))
    except OSError:
        checks["active_sessions"] = -1
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
