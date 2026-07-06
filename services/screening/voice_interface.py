from typing import Dict, List, Protocol


class Turn:
    def __init__(self, role: str, text: str):
        self.role = role
        self.text = text


class VoiceInterface(Protocol):
    def send_message(self, session_id: str, text: str) -> None: ...
    def end_session(self, session_id: str) -> None: ...
    def get_transcript(self, session_id: str) -> List[Turn]: ...


class StubVoiceInterface:
    """Stub matching Aidan/Twilio pattern. Replace with real implementation."""

    def __init__(self):
        self._messages: Dict[str, List[str]] = {}
        self._transcripts: Dict[str, List[Turn]] = {}
        self._ended: set = set()

    def send_message(self, session_id: str, text: str) -> None:
        if session_id not in self._messages:
            self._messages[session_id] = []
        self._messages[session_id].append(text)

    def end_session(self, session_id: str) -> None:
        self._ended.add(session_id)

    def get_transcript(self, session_id: str) -> List[Turn]:
        return self._transcripts.get(session_id, [])

    def get_sent_messages(self, session_id: str) -> List[str]:
        return self._messages.get(session_id, [])

    def is_ended(self, session_id: str) -> bool:
        return session_id in self._ended
