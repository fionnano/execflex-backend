from enum import Enum
from typing import Dict, List, Optional, Protocol

from .models import (
    Answer,
    Question,
    ScreeningOutcome,
    ScreeningSession,
    ScreeningSessionType,
    StructuredBrief,
)


class ScreeningState(Enum):
    IDLE = "idle"
    CONSENT = "consent"
    INTAKE = "intake"
    SCORING = "scoring"
    HANDOFF = "handoff"
    COMPLETE = "complete"


HANDOFF_PHRASES = [
    "speak to a human",
    "talk to someone",
    "real person",
    "speak to a person",
    "human please",
    "transfer me",
    "i want a person",
    "let me talk to",
    "stop the bot",
]

DISTRESS_PHRASES = [
    "lawyer",
    "solicitor",
    "legal",
    "sue",
    "discrimination",
    "harassment",
    "complaint",
    "unfair",
]

MAX_TURNS_WITHOUT_PROGRESS = 3
MIN_CONFIDENCE_THRESHOLD = 0.3


class VoiceInterface(Protocol):
    def send_message(self, session_id: str, text: str) -> None: ...
    def end_session(self, session_id: str) -> None: ...
    def get_transcript(self, session_id: str) -> list: ...


class ScreeningStateMachine:
    def __init__(self, session: ScreeningSession, voice: Optional[VoiceInterface] = None):
        self._session = session
        self._voice = voice
        self._state = ScreeningState.IDLE
        self._turns_without_progress = 0
        self._transition_log: List[Dict] = []

    @property
    def state(self) -> ScreeningState:
        return self._state

    @property
    def session(self) -> ScreeningSession:
        return self._session

    @property
    def transitions(self) -> List[Dict]:
        return list(self._transition_log)

    def _transition(self, new_state: ScreeningState, reason: str = "") -> None:
        old_state = self._state
        self._state = new_state
        self._transition_log.append({
            "from": old_state.value,
            "to": new_state.value,
            "reason": reason,
        })

    def start(self) -> str:
        if self._state != ScreeningState.IDLE:
            raise ValueError(f"Cannot start from state {self._state.value}")

        self._transition(ScreeningState.CONSENT, "session_started")

        if self._session.session_type == ScreeningSessionType.CANDIDATE:
            return (
                "Before we begin, I need to let you know that this conversation "
                "may be recorded and analysed by AI to assess your suitability for "
                "the role. Your data will be handled in accordance with GDPR. "
                "Do you consent to proceed?"
            )
        else:
            return (
                "Before we begin, I need to let you know that this conversation "
                "may be recorded and analysed to help us understand your hiring needs. "
                "Do you consent to proceed?"
            )

    def give_consent(self, consented: bool) -> str:
        if self._state != ScreeningState.CONSENT:
            raise ValueError(f"Cannot give consent from state {self._state.value}")

        if not consented:
            self._transition(ScreeningState.HANDOFF, "consent_declined")
            self._session.handoff_reason = "Consent declined"
            return (
                "No problem. I'll connect you with a team member who can help. "
                "Thank you for your time."
            )

        self._session.consent_given = True
        self._transition(ScreeningState.INTAKE, "consent_given")

        question = self._session.current_question
        if question:
            return question.text
        self._transition(ScreeningState.SCORING, "no_questions")
        return "Thank you. Let me process your information."

    def answer(self, response_text: str) -> str:
        if self._state != ScreeningState.INTAKE:
            raise ValueError(f"Cannot answer from state {self._state.value}")

        if self._should_handoff(response_text):
            return self._do_handoff(response_text)

        question = self._session.current_question
        if not question:
            self._transition(ScreeningState.SCORING, "all_questions_answered")
            return "Thank you. Let me process your information."

        ans = Answer(
            question_id=question.id,
            response_text=response_text,
        )
        self._session.answers.append(ans)
        self._session.current_question_index += 1

        if response_text.strip() and len(response_text.strip()) > 5:
            self._turns_without_progress = 0
        else:
            self._turns_without_progress += 1

        if self._turns_without_progress >= MAX_TURNS_WITHOUT_PROGRESS:
            return self._do_handoff("No progress after multiple turns")

        next_q = self._session.current_question
        if next_q:
            return next_q.text

        self._transition(ScreeningState.SCORING, "all_questions_answered")
        return "Thank you for your responses. Let me review everything."

    def score(self) -> ScreeningOutcome:
        if self._state != ScreeningState.SCORING:
            if self._state == ScreeningState.INTAKE and self._session.is_complete:
                self._transition(ScreeningState.SCORING, "forced_score")
            else:
                raise ValueError(f"Cannot score from state {self._state.value}")

        answers = self._session.answers
        if not answers:
            outcome = ScreeningOutcome(
                recommendation="hold",
                overall_score=0.0,
                answers=[],
                summary="No answers recorded",
            )
            self._session.outcome = outcome
            self._transition(ScreeningState.COMPLETE, "scored_empty")
            return outcome

        total_weight = 0.0
        weighted_score = 0.0
        extracted = {}

        for ans in answers:
            q = next((q for q in self._session.questions if q.id == ans.question_id), None)
            weight = q.weight if q else 1.0
            category = q.category if q else "unknown"

            score = self._heuristic_score(ans.response_text)
            ans.score = score
            ans.score_justification = self._score_justification(score)

            facts = self._extract_facts(category, ans.response_text)
            ans.extracted_facts = facts
            extracted.update(facts)

            weighted_score += score * weight
            total_weight += weight

        overall = (weighted_score / total_weight) if total_weight > 0 else 0.0

        if overall >= 4.0:
            recommendation = "strong_proceed"
        elif overall >= 3.0:
            recommendation = "proceed"
        elif overall >= 2.0:
            recommendation = "hold"
        else:
            recommendation = "reject"

        outcome = ScreeningOutcome(
            recommendation=recommendation,
            overall_score=round(overall, 2),
            answers=answers,
            extracted_facts=extracted,
            summary=f"Screening complete. Recommendation: {recommendation} (score: {overall:.1f}/5)",
        )

        self._session.outcome = outcome
        self._transition(ScreeningState.COMPLETE, "scored")
        return outcome

    def build_brief(self) -> StructuredBrief:
        if self._session.session_type != ScreeningSessionType.CLIENT:
            raise ValueError("build_brief only for client sessions")

        if self._state != ScreeningState.SCORING:
            if self._state == ScreeningState.INTAKE and self._session.is_complete:
                self._transition(ScreeningState.SCORING, "forced_brief")
            else:
                raise ValueError(f"Cannot build brief from state {self._state.value}")

        brief = StructuredBrief(raw_answers=list(self._session.answers))

        for ans in self._session.answers:
            q = next((q for q in self._session.questions if q.id == ans.question_id), None)
            if not q:
                continue
            cat = q.category
            text = ans.response_text.strip()

            if cat == "role_description":
                brief.role_title = text.split(".")[0] if "." in text else text
                brief.role_description = text
            elif cat == "requirements":
                brief.requirements_must_have = [r.strip() for r in text.split(",") if r.strip()]
            elif cat == "nice_to_have":
                brief.requirements_nice_to_have = [r.strip() for r in text.split(",") if r.strip()]
            elif cat == "team":
                brief.team_size = text
            elif cat == "timeline":
                brief.timeline = text
            elif cat == "budget":
                brief.budget_range = text
            elif cat == "culture":
                brief.culture_notes = text

        self._session.brief = brief
        self._transition(ScreeningState.COMPLETE, "brief_built")
        return brief

    def _should_handoff(self, text: str) -> bool:
        lower = text.lower()
        for phrase in HANDOFF_PHRASES:
            if phrase in lower:
                return True
        for phrase in DISTRESS_PHRASES:
            if phrase in lower:
                return True
        return False

    def _do_handoff(self, reason: str) -> str:
        self._session.handoff_reason = reason
        self._transition(ScreeningState.HANDOFF, f"handoff: {reason}")
        return (
            "I understand. Let me connect you with a team member who can assist you directly. "
            "Please hold on."
        )

    @staticmethod
    def _heuristic_score(text: str) -> float:
        if not text or not text.strip():
            return 1.0

        length = len(text.strip())
        if length < 10:
            return 2.0
        elif length < 50:
            return 3.0
        elif length < 200:
            return 3.5
        else:
            return 4.0

    @staticmethod
    def _score_justification(score: float) -> str:
        if score >= 4.0:
            return "Detailed response provided"
        elif score >= 3.0:
            return "Adequate response"
        elif score >= 2.0:
            return "Brief response, limited detail"
        else:
            return "Minimal or no response"

    @staticmethod
    def _extract_facts(category: str, text: str) -> dict:
        if not text or not text.strip():
            return {}

        facts = {}
        lower = text.lower()

        if category == "experience":
            import re
            years_match = re.search(r"(\d+)\s*(?:years?|yrs?)", lower)
            if years_match:
                facts["experience_years"] = int(years_match.group(1))

        elif category == "location":
            facts["location"] = text.strip()
            if "remote" in lower:
                facts["remote_ok"] = True

        elif category == "availability":
            facts["availability"] = text.strip()
            for term in ["full-time", "full time", "part-time", "part time", "contract", "fractional", "interim"]:
                if term in lower:
                    facts["availability_type"] = term.replace(" ", "_")
                    break

        elif category == "compensation":
            import re
            nums = re.findall(r"[\d,]+", text)
            if nums:
                facts["compensation_mentioned"] = nums[0].replace(",", "")

        elif category == "skills":
            facts["skills_raw"] = text.strip()

        elif category == "motivation":
            facts["motivation"] = text.strip()

        elif category == "background":
            facts["background"] = text.strip()

        return facts
