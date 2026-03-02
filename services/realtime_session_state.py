"""
Realtime Session State - In-memory state for streaming voice calls.
Maintains minimal synchronous state per call for conversation context.
"""
import time
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from threading import Lock
from enum import Enum


class CallPhase(Enum):
    """Phases of a qualification call."""
    CONNECTING = "connecting"
    GREETING = "greeting"
    DISCOVERY = "discovery"
    CLOSING = "closing"
    ENDED = "ended"


@dataclass
class UserFacts:
    """Facts discovered about the user during the call."""
    name: Optional[str] = None
    role_type: Optional[str] = None  # talent or hirer
    motivation: Optional[str] = None  # Why they're on ExecFlex
    role_targets: List[str] = field(default_factory=list)  # Roles interested in
    industry_focus: List[str] = field(default_factory=list)  # Industries
    location: Optional[str] = None
    availability: Optional[str] = None  # fractional, full-time, etc.
    constraints: List[str] = field(default_factory=list)  # Deal breakers
    urgency: Optional[str] = None  # How soon they need something

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role_type": self.role_type,
            "motivation": self.motivation,
            "role_targets": self.role_targets,
            "industry_focus": self.industry_focus,
            "location": self.location,
            "availability": self.availability,
            "constraints": self.constraints,
            "urgency": self.urgency
        }

    def to_context_string(self) -> str:
        """Generate a summary string for LLM context."""
        parts = []
        if self.name:
            parts.append(f"Name: {self.name}")
        if self.role_type:
            parts.append(f"Type: {self.role_type}")
        if self.motivation:
            parts.append(f"Motivation: {self.motivation}")
        if self.role_targets:
            parts.append(f"Target roles: {', '.join(self.role_targets)}")
        if self.industry_focus:
            parts.append(f"Industries: {', '.join(self.industry_focus)}")
        if self.location:
            parts.append(f"Location: {self.location}")
        if self.availability:
            parts.append(f"Availability: {self.availability}")
        if self.constraints:
            parts.append(f"Constraints: {', '.join(self.constraints)}")
        if self.urgency:
            parts.append(f"Urgency: {self.urgency}")
        return "; ".join(parts) if parts else "No facts collected yet"


@dataclass
class RealtimeSessionState:
    """
    Tiny synchronous state for a streaming voice call.
    Kept minimal to avoid blocking during real-time audio processing.
    """
    call_sid: str
    job_id: Optional[str] = None
    interaction_id: Optional[str] = None
    user_id: Optional[str] = None
    signup_mode: Optional[str] = None

    # Call lifecycle
    phase: CallPhase = CallPhase.CONNECTING
    start_time: float = field(default_factory=time.time)
    max_duration_seconds: int = 600  # 10 minutes

    # Question tracking
    questions_asked: Set[str] = field(default_factory=set)
    question_last_asked: Optional[str] = None
    turn_count: int = 0

    # User facts (discovered during conversation)
    facts: UserFacts = field(default_factory=UserFacts)

    # Intent tracking
    last_user_intent: Optional[str] = None
    last_user_text: Optional[str] = None

    # Retry/error tracking
    consecutive_errors: int = 0
    total_retries: int = 0
    max_retries: int = 2

    # Conversation history for rolling summary
    recent_turns: List[Dict[str, str]] = field(default_factory=list)
    turns_since_summary: int = 0
    summary_interval: int = 3

    # Audio state
    is_assistant_speaking: bool = False
    pending_user_audio: bool = False

    def is_expired(self) -> bool:
        """Check if call has exceeded max duration."""
        return (time.time() - self.start_time) > self.max_duration_seconds

    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time

    def remaining_seconds(self) -> float:
        """Get remaining time in seconds."""
        return max(0, self.max_duration_seconds - self.elapsed_seconds())

    def should_summarize(self) -> bool:
        """Check if we should generate a rolling summary."""
        return self.turns_since_summary >= self.summary_interval

    def record_question(self, question_type: str) -> None:
        """Record that a question was asked."""
        self.questions_asked.add(question_type)
        self.question_last_asked = question_type

    def has_asked(self, question_type: str) -> bool:
        """Check if a question type has been asked."""
        return question_type in self.questions_asked

    def add_turn(self, speaker: str, text: str) -> None:
        """Add a turn to recent history."""
        self.recent_turns.append({"speaker": speaker, "text": text})
        self.turn_count += 1
        if speaker == "assistant":
            self.turns_since_summary += 1
        # Keep only last 10 turns in memory
        if len(self.recent_turns) > 10:
            self.recent_turns = self.recent_turns[-10:]

    def reset_summary_counter(self) -> None:
        """Reset the summary counter after generating a summary."""
        self.turns_since_summary = 0

    def record_error(self) -> bool:
        """
        Record an error and return whether retry is allowed.
        Returns True if retry is allowed, False if limit exceeded.
        """
        self.consecutive_errors += 1
        self.total_retries += 1
        return self.consecutive_errors <= self.max_retries

    def clear_errors(self) -> None:
        """Clear consecutive error count after successful operation."""
        self.consecutive_errors = 0

    def update_facts(self, updates: Dict[str, Any]) -> None:
        """Update user facts from extracted data."""
        if "name" in updates and updates["name"]:
            self.facts.name = updates["name"]
        if "role_type" in updates and updates["role_type"]:
            self.facts.role_type = updates["role_type"]
        if "motivation" in updates and updates["motivation"]:
            self.facts.motivation = updates["motivation"]
        if "role_targets" in updates and updates["role_targets"]:
            for r in updates["role_targets"]:
                if r not in self.facts.role_targets:
                    self.facts.role_targets.append(r)
        if "industry_focus" in updates and updates["industry_focus"]:
            for i in updates["industry_focus"]:
                if i not in self.facts.industry_focus:
                    self.facts.industry_focus.append(i)
        if "location" in updates and updates["location"]:
            self.facts.location = updates["location"]
        if "availability" in updates and updates["availability"]:
            self.facts.availability = updates["availability"]
        if "constraints" in updates and updates["constraints"]:
            for c in updates["constraints"]:
                if c not in self.facts.constraints:
                    self.facts.constraints.append(c)
        if "urgency" in updates and updates["urgency"]:
            self.facts.urgency = updates["urgency"]

    def get_system_context(self) -> str:
        """Get context string for LLM system prompt."""
        parts = [
            f"Call phase: {self.phase.value}",
            f"Time remaining: {int(self.remaining_seconds())} seconds",
            f"Turn count: {self.turn_count}",
        ]
        if self.signup_mode:
            parts.append(f"User type: {self.signup_mode}")
        if self.facts.to_context_string() != "No facts collected yet":
            parts.append(f"Known facts: {self.facts.to_context_string()}")
        if self.questions_asked:
            parts.append(f"Topics covered: {', '.join(self.questions_asked)}")
        return "\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize state to dict."""
        return {
            "call_sid": self.call_sid,
            "job_id": self.job_id,
            "interaction_id": self.interaction_id,
            "user_id": self.user_id,
            "signup_mode": self.signup_mode,
            "phase": self.phase.value,
            "elapsed_seconds": self.elapsed_seconds(),
            "turn_count": self.turn_count,
            "questions_asked": list(self.questions_asked),
            "facts": self.facts.to_dict(),
            "last_user_intent": self.last_user_intent,
            "consecutive_errors": self.consecutive_errors
        }


class SessionStateManager:
    """
    Thread-safe manager for all active call sessions.
    """

    def __init__(self):
        self._sessions: Dict[str, RealtimeSessionState] = {}
        self._lock = Lock()

    def create_session(
        self,
        call_sid: str,
        *,
        job_id: Optional[str] = None,
        interaction_id: Optional[str] = None,
        user_id: Optional[str] = None,
        signup_mode: Optional[str] = None
    ) -> RealtimeSessionState:
        """Create a new session for a call."""
        with self._lock:
            session = RealtimeSessionState(
                call_sid=call_sid,
                job_id=job_id,
                interaction_id=interaction_id,
                user_id=user_id,
                signup_mode=signup_mode
            )
            self._sessions[call_sid] = session
            return session

    def get_session(self, call_sid: str) -> Optional[RealtimeSessionState]:
        """Get session by call SID."""
        with self._lock:
            return self._sessions.get(call_sid)

    def get_or_create_session(
        self,
        call_sid: str,
        **kwargs
    ) -> RealtimeSessionState:
        """Get existing session or create new one."""
        with self._lock:
            if call_sid in self._sessions:
                return self._sessions[call_sid]
            session = RealtimeSessionState(call_sid=call_sid, **kwargs)
            self._sessions[call_sid] = session
            return session

    def end_session(self, call_sid: str) -> Optional[RealtimeSessionState]:
        """End and remove a session. Returns the final state."""
        with self._lock:
            session = self._sessions.pop(call_sid, None)
            if session:
                session.phase = CallPhase.ENDED
            return session

    def get_active_sessions(self) -> List[str]:
        """Get list of active call SIDs."""
        with self._lock:
            return list(self._sessions.keys())

    def cleanup_expired(self) -> List[str]:
        """Remove expired sessions. Returns list of removed call SIDs."""
        with self._lock:
            expired = [
                sid for sid, session in self._sessions.items()
                if session.is_expired()
            ]
            for sid in expired:
                self._sessions.pop(sid, None)
            return expired


# Global singleton
_session_manager: Optional[SessionStateManager] = None


def get_session_manager() -> SessionStateManager:
    """Get the global session state manager singleton."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionStateManager()
    return _session_manager
