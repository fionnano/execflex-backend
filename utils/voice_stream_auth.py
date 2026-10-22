"""
Authenticate the Twilio Media Stream WebSocket.

``/voice/stream`` returns TwiML that points Twilio at ``/voice/ws``. That
socket used to accept any caller who knew a job id and then open an OpenAI
Realtime session on the server key. The TwiML now carries an HMAC bound to
the job id and an expiry. The socket checks it before reading the job or
contacting OpenAI.

The signing key is VOICE_STREAM_SIGNING_SECRET, or TWILIO_AUTH_TOKEN when
that override is unset. If neither is configured the stream is refused,
unless TWILIO_ALLOW_UNSIGNED_WEBHOOKS is explicitly enabled outside production.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

from utils.security_env import unsigned_twilio_webhooks_allowed

_TOKEN_TTL_SECONDS = 4 * 60 * 60
_MIN_KEY_LENGTH = 16


def stream_auth_required() -> bool:
    return not unsigned_twilio_webhooks_allowed()


def _signing_key() -> Optional[bytes]:
    secret = (
        os.getenv("VOICE_STREAM_SIGNING_SECRET")
        or os.getenv("TWILIO_AUTH_TOKEN")
        or ""
    ).strip()
    if len(secret) < _MIN_KEY_LENGTH:
        return None
    return secret.encode("utf-8")


def make_stream_token(job_id: str, now: Optional[float] = None) -> str:
    """Return ``expires.hmac`` for job_id, or '' when unsigned local mode is on."""
    key = _signing_key()
    if not key:
        if not stream_auth_required():
            return ""
        raise RuntimeError("Voice stream signing key is not configured")
    expires = int((now if now is not None else time.time()) + _TOKEN_TTL_SECONDS)
    message = f"voice-stream-v1|{job_id}|{expires}".encode("utf-8")
    signature = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def verify_stream_token(
    job_id: Optional[str],
    token: Optional[str],
    now: Optional[float] = None,
) -> bool:
    """True only for a token this process would have issued for job_id."""
    if not stream_auth_required():
        return True
    if not job_id or not token:
        return False
    expires_raw, sep, signature = token.partition(".")
    if not sep or not expires_raw.isdigit() or not signature:
        return False
    expires = int(expires_raw)
    if (now if now is not None else time.time()) > expires:
        return False
    key = _signing_key()
    if not key:
        return False
    message = f"voice-stream-v1|{job_id}|{expires}".encode("utf-8")
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8"))
