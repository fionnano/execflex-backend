"""
Cara voice session management.

POST /voice-session/cara — create a session with a system prompt.
Returns session_id + a signed WebSocket URL for the browser to connect to.

Auth: a Supabase JWT or the X-Service-Key shared with ainm's backend. The
Origin header is never treated as authentication (SEC-E-001): any non-browser
client can set it. The returned ws_url carries a short-lived HMAC token bound
to the session id; routes/cara_websocket.py verifies it before bridging to
OpenAI Realtime. Session creation is quota-limited per identity and per IP.

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
import hmac
import hashlib
import secrets
import threading
from typing import Optional
from flask import Blueprint, request, jsonify

from config.app_config import OPENAI_API_KEY

cara_bp = Blueprint("cara", __name__)

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
        _prune_quota_hits()
        if removed:
            _log(None, "cleanup", removed=removed)


_cleanup_thread = threading.Thread(target=_cleanup_expired, daemon=True)
_cleanup_thread.start()


# ── Signed WebSocket URLs ─────────────────────────────────────────────────────
# The ws_url returned by POST /voice-session/cara carries "<expires>.<hmac>",
# an HMAC-SHA256 over the session id and expiry. The WebSocket handler verifies
# it before touching the session, so a session id alone (guessed, leaked or
# replayed) is not enough to open an OpenAI Realtime bridge on the server key.
_WS_TOKEN_TTL = _SESSION_TTL
_WS_SIGNING_KEY_FILE = os.path.join("/tmp", "cara_ws_signing_key")


def _ws_signing_key() -> bytes:
    """
    Key used to sign ws tokens.

    CARA_WS_SIGNING_SECRET when configured. Otherwise a random key generated
    once per container and kept on the filesystem, which the HTTP and
    WebSocket processes share even though they share no memory. A restart
    rotates it, which only invalidates tokens younger than the session TTL.
    """
    configured = os.getenv("CARA_WS_SIGNING_SECRET")
    if configured:
        return configured.encode("utf-8")

    try:
        with open(_WS_SIGNING_KEY_FILE, "rb") as f:
            key = f.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass

    # Write to a private temp file, then atomically move it into place so
    # concurrent first callers converge on a single key.
    tmp_path = f"{_WS_SIGNING_KEY_FILE}.{os.getpid()}.{secrets.token_hex(4)}"
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_hex(32).encode("ascii"))
        os.replace(tmp_path, _WS_SIGNING_KEY_FILE)
    except OSError:
        _safe_unlink(tmp_path)
    with open(_WS_SIGNING_KEY_FILE, "rb") as f:
        return f.read().strip()


def _ws_signature(session_id: str, expires: int) -> str:
    message = f"cara-ws-v1|{session_id}|{expires}".encode("utf-8")
    return hmac.new(_ws_signing_key(), message, hashlib.sha256).hexdigest()


def make_ws_token(session_id: str, now: Optional[float] = None) -> str:
    """Return a token authorising one WebSocket connection to session_id."""
    expires = int((now if now is not None else time.time()) + _WS_TOKEN_TTL)
    return f"{expires}.{_ws_signature(session_id, expires)}"


def verify_ws_token(session_id: str, token: Optional[str], now: Optional[float] = None) -> bool:
    """True only if token was issued for this session_id and has not expired."""
    if not session_id or not token:
        return False
    expires_raw, sep, signature = token.partition(".")
    if not sep or not expires_raw.isdigit() or not signature:
        return False
    expires = int(expires_raw)
    if (now if now is not None else time.time()) > expires:
        return False
    try:
        expected = _ws_signature(session_id, expires)
    except OSError:
        return False
    # Compare as bytes: compare_digest() rejects non-ASCII str input with a
    # TypeError, which an attacker-supplied token must not be able to trigger.
    return hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8"))


# ── Session quotas ────────────────────────────────────────────────────────────
# Sliding one-hour windows. Every caller is capped per authenticated identity;
# JWT users are additionally capped per client IP. Service callers (ainm's
# backend, the uptime monitor) act for many end users from one address, so
# they get a separate, larger per-identity cap instead of the per-IP one.
_QUOTA_WINDOW = 3600
_quota_hits: dict = {}
_quota_lock = threading.Lock()


def _quota_limit(env_name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(env_name, default)))
    except (TypeError, ValueError):
        return default


def _within_quota(key: str, limit: int) -> bool:
    """Record a hit for key; False once `limit` hits already fall in the window."""
    now = time.time()
    cutoff = now - _QUOTA_WINDOW
    with _quota_lock:
        hits = [t for t in _quota_hits.get(key, []) if t > cutoff]
        if len(hits) >= limit:
            _quota_hits[key] = hits
            return False
        hits.append(now)
        _quota_hits[key] = hits
        return True


def _prune_quota_hits() -> None:
    cutoff = time.time() - _QUOTA_WINDOW
    with _quota_lock:
        for key in list(_quota_hits):
            hits = [t for t in _quota_hits[key] if t > cutoff]
            if hits:
                _quota_hits[key] = hits
            else:
                del _quota_hits[key]


def _check_session_quota(identity: str, remote_ip: str) -> Optional[str]:
    """Return None if the caller may create a session, else the exhausted quota."""
    if identity.startswith("service:"):
        limit = _quota_limit("CARA_SESSION_LIMIT_PER_SERVICE", 600)
        return None if _within_quota(f"id:{identity}", limit) else "service"
    if not _within_quota(f"id:{identity}", _quota_limit("CARA_SESSION_LIMIT_PER_USER", 30)):
        return "user"
    if not _within_quota(f"ip:{remote_ip}", _quota_limit("CARA_SESSION_LIMIT_PER_IP", 60)):
        return "ip"
    return None


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

    Auth: Supabase JWT (Authorization: Bearer) or X-Service-Key. The Origin
    header is not authentication and is ignored for that purpose.

    Body (JSON):
        system_prompt   str  — Full system prompt for Cara

    Returns:
        201 { session_id, ws_url }   ws_url carries a signed, short-lived token
        401 unauthenticated
        429 the caller's session quota for the hour is used up
    """
    from utils.auth_helpers import get_authenticated_user_id
    user_id, _ = get_authenticated_user_id()
    if not user_id:
        _log(None, "REJECTED", reason="unauthenticated",
             origin=repr(request.headers.get("Origin") or ""))
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(force=True, silent=True) or {}
    system_prompt = (data.get("system_prompt") or "").strip()
    if not system_prompt:
        return jsonify({"error": "system_prompt is required"}), 400

    is_service = user_id.startswith("service:")
    exhausted = _check_session_quota(user_id, request.remote_addr or "unknown")
    if exhausted:
        _log(None, "QUOTA_EXCEEDED", quota=exhausted,
             identity=user_id if is_service else user_id[:8])
        return jsonify({"error": "Voice session quota exceeded; try again later"}), 429

    session_id = str(uuid.uuid4())
    try:
        token = make_ws_token(session_id)
    except OSError as e:
        _log(session_id, "SIGNING_KEY_UNAVAILABLE", error=f"{type(e).__name__}: {e}")
        return jsonify({"error": "Voice sessions temporarily unavailable"}), 503
    _store_session(session_id, system_prompt)

    base_url = os.getenv("EXECFLEX_BASE_URL", "wss://execflex-backend-1.onrender.com")
    base_url = base_url.rstrip("/")
    if base_url.startswith("https://"):
        base_url = "wss://" + base_url[8:]
    elif base_url.startswith("http://"):
        base_url = "ws://" + base_url[7:]

    ws_url = f"{base_url}/voice/cara/ws/{session_id}?token={token}"

    _log(session_id, "SESSION_CREATED", prompt_len=len(system_prompt),
         auth="service" if is_service else "jwt")

    return jsonify({
        "session_id": session_id,
        "ws_url": ws_url,
    }), 201
