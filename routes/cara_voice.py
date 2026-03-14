"""
Cara voice session management.

POST /voice-session/cara — create a session with a system prompt.
Returns session_id + WebSocket URL for the browser to connect to.

The system prompt is encoded directly in the WebSocket URL as a compressed
base64url query parameter — stateless, works across multiple Render instances.
"""
import os
import uuid
import zlib
import base64
from flask import Blueprint, request, jsonify

cara_bp = Blueprint("cara", __name__)


def encode_system_prompt(system_prompt: str) -> str:
    """Compress and base64url-encode a system prompt for embedding in a URL."""
    compressed = zlib.compress(system_prompt.encode("utf-8"), level=9)
    return base64.urlsafe_b64encode(compressed).decode("ascii")


def decode_system_prompt(encoded: str) -> str:
    """Decode and decompress a system prompt from a URL parameter."""
    compressed = base64.urlsafe_b64decode(encoded)
    return zlib.decompress(compressed).decode("utf-8")


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

    session_id = str(uuid.uuid4())

    # Encode system prompt directly into the WebSocket URL so any Render instance
    # can handle the connection — no shared in-memory state required.
    encoded_prompt = encode_system_prompt(system_prompt)

    base_url = os.getenv("EXECFLEX_BASE_URL", "wss://execflex-backend-1.onrender.com")
    base_url = base_url.rstrip("/")
    if base_url.startswith("https://"):
        base_url = "wss://" + base_url[8:]
    elif base_url.startswith("http://"):
        base_url = "ws://" + base_url[7:]

    ws_url = f"{base_url}/voice/cara/ws/{session_id}?sp={encoded_prompt}"

    print(f"[Cara] Created session {session_id}, encoded prompt len={len(encoded_prompt)}", flush=True)

    return jsonify({
        "session_id": session_id,
        "ws_url": ws_url,
    }), 201
