"""
Voice Metrics Service - Telemetry for realtime streaming calls.
Tracks latency, events, and errors for post-call analysis.
"""
import time
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from threading import Lock
from config.clients import supabase_client


@dataclass
class TurnMetrics:
    """Metrics for a single conversation turn."""
    turn_seq: int
    start_time: float = field(default_factory=time.time)
    user_speech_end_time: Optional[float] = None
    first_audio_time: Optional[float] = None
    response_complete_time: Optional[float] = None

    @property
    def time_to_first_audio_ms(self) -> Optional[int]:
        """Time from user speech end to first assistant audio."""
        if self.user_speech_end_time and self.first_audio_time:
            return int((self.first_audio_time - self.user_speech_end_time) * 1000)
        return None

    @property
    def silence_gap_ms(self) -> Optional[int]:
        """Same as time_to_first_audio for now."""
        return self.time_to_first_audio_ms


@dataclass
class CallMetrics:
    """Aggregated metrics for a complete call."""
    call_sid: str
    job_id: Optional[str] = None
    interaction_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    turns: List[TurnMetrics] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> Optional[int]:
        if self.end_time:
            return int((self.end_time - self.start_time) * 1000)
        return None


class VoiceMetricsService:
    """
    Service for tracking voice call telemetry.
    Buffers metrics in memory and persists to Supabase.
    """

    def __init__(self):
        self._calls: Dict[str, CallMetrics] = {}
        self._lock = Lock()

    def start_call(
        self,
        call_sid: str,
        job_id: Optional[str] = None,
        interaction_id: Optional[str] = None
    ) -> CallMetrics:
        """Initialize metrics tracking for a new call."""
        with self._lock:
            metrics = CallMetrics(
                call_sid=call_sid,
                job_id=job_id,
                interaction_id=interaction_id
            )
            self._calls[call_sid] = metrics
            self._record_event(call_sid, "call_started", provider="system")
            return metrics

    def get_call(self, call_sid: str) -> Optional[CallMetrics]:
        """Get metrics for a call."""
        with self._lock:
            return self._calls.get(call_sid)

    def start_turn(self, call_sid: str, turn_seq: int) -> Optional[TurnMetrics]:
        """Start tracking a new conversation turn."""
        with self._lock:
            call = self._calls.get(call_sid)
            if not call:
                return None
            turn = TurnMetrics(turn_seq=turn_seq)
            call.turns.append(turn)
            return turn

    def record_user_speech_end(self, call_sid: str) -> None:
        """Mark when user finished speaking."""
        with self._lock:
            call = self._calls.get(call_sid)
            if call and call.turns:
                call.turns[-1].user_speech_end_time = time.time()

    def record_first_audio(self, call_sid: str) -> Optional[int]:
        """Mark when first assistant audio was sent. Returns latency_ms."""
        with self._lock:
            call = self._calls.get(call_sid)
            if call and call.turns:
                turn = call.turns[-1]
                turn.first_audio_time = time.time()
                latency_ms = turn.time_to_first_audio_ms
                self._record_event(
                    call_sid,
                    "first_audio",
                    latency_ms=latency_ms,
                    provider="system",
                    turn_seq=turn.turn_seq
                )
                return latency_ms
        return None

    def record_response_complete(self, call_sid: str) -> None:
        """Mark when assistant response finished."""
        with self._lock:
            call = self._calls.get(call_sid)
            if call and call.turns:
                call.turns[-1].response_complete_time = time.time()

    def record_event(
        self,
        call_sid: str,
        event_name: str,
        *,
        latency_ms: Optional[int] = None,
        silence_gap_ms: Optional[int] = None,
        status: str = "ok",
        provider: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        turn_seq: Optional[int] = None
    ) -> None:
        """Record a generic event for the call."""
        self._record_event(
            call_sid, event_name,
            latency_ms=latency_ms,
            silence_gap_ms=silence_gap_ms,
            status=status,
            provider=provider,
            metadata=metadata,
            turn_seq=turn_seq
        )

    def _record_event(
        self,
        call_sid: str,
        event_name: str,
        *,
        latency_ms: Optional[int] = None,
        silence_gap_ms: Optional[int] = None,
        status: str = "ok",
        provider: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        turn_seq: Optional[int] = None
    ) -> None:
        """Internal: record event to in-memory buffer."""
        call = self._calls.get(call_sid)
        event = {
            "id": str(uuid.uuid4()),
            "created_at": time.time(),
            "call_sid": call_sid,
            "job_id": call.job_id if call else None,
            "interaction_id": call.interaction_id if call else None,
            "turn_seq": turn_seq,
            "event_name": event_name,
            "latency_ms": latency_ms,
            "silence_gap_ms": silence_gap_ms,
            "status": status,
            "provider": provider,
            "metadata": metadata or {}
        }
        if call:
            call.events.append(event)

    def end_call(self, call_sid: str, status: str = "ok") -> None:
        """
        Mark call as ended and persist all metrics to Supabase.
        """
        with self._lock:
            call = self._calls.get(call_sid)
            if not call:
                return

            call.end_time = time.time()
            self._record_event(
                call_sid,
                "call_ended",
                status=status,
                provider="system",
                metadata={"duration_ms": call.duration_ms}
            )

        # Persist to Supabase (outside lock)
        self._persist_call_metrics(call_sid)

        # Clean up
        with self._lock:
            self._calls.pop(call_sid, None)

    def _persist_call_metrics(self, call_sid: str) -> None:
        """Persist all buffered events for a call to Supabase."""
        call = self._calls.get(call_sid)
        if not call or not call.events:
            return

        try:
            rows = []
            for event in call.events:
                rows.append({
                    "call_sid": event["call_sid"],
                    "job_id": event.get("job_id"),
                    "interaction_id": event.get("interaction_id"),
                    "turn_seq": event.get("turn_seq"),
                    "event_name": event["event_name"],
                    "latency_ms": event.get("latency_ms"),
                    "silence_gap_ms": event.get("silence_gap_ms"),
                    "status": event.get("status", "ok"),
                    "provider": event.get("provider"),
                    "metadata": event.get("metadata", {})
                })

            if rows:
                supabase_client.table("voice_call_metrics").insert(rows).execute()
                print(f"Persisted {len(rows)} metrics events for call {call_sid}")
        except Exception as e:
            print(f"Failed to persist metrics for call {call_sid}: {e}")

    def get_turn_latencies(self, call_sid: str) -> List[Dict[str, Any]]:
        """Get latency stats for all turns in a call."""
        call = self._calls.get(call_sid)
        if not call:
            return []

        return [
            {
                "turn_seq": t.turn_seq,
                "time_to_first_audio_ms": t.time_to_first_audio_ms,
                "silence_gap_ms": t.silence_gap_ms
            }
            for t in call.turns
        ]


# Global singleton
_metrics_service: Optional[VoiceMetricsService] = None


def get_metrics_service() -> VoiceMetricsService:
    """Get the global metrics service singleton."""
    global _metrics_service
    if _metrics_service is None:
        _metrics_service = VoiceMetricsService()
    return _metrics_service
