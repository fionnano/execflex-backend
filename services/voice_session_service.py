"""
Voice session management for tracking conversation state during calls.
"""
from typing import Dict, Any

# In-memory session store (keyed by CallSid)
SESSIONS: Dict[str, Dict[str, Any]] = {}


def init_session(call_sid: str) -> Dict[str, Any]:
    """Initialize or retrieve a session for a call."""
    if call_sid not in SESSIONS:
        SESSIONS[call_sid] = {
            "user_type": None,
            "name": None,
            "email": None,
            "role": None,
            "industry": None,
            "location": None,
            "availability": None,
            "__match": None,
            "_retries": {}
        }
    return SESSIONS[call_sid]


def get_session(call_sid: str) -> Dict[str, Any] | None:
    """Get an existing session."""
    return SESSIONS.get(call_sid)


def clear_session(call_sid: str):
    """Clear a session (optional cleanup)."""
    if call_sid in SESSIONS:
        del SESSIONS[call_sid]

