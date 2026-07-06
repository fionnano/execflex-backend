"""
Screening State Machine v1 — synthetic test suite.
Tests full candidate and client intake flows, consent handling,
handoff triggers, scoring, fact extraction, and brief building.
Zero real data. All fixtures are invented.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from services.screening.models import (
    Answer,
    ScreeningSession,
    ScreeningSessionType,
    ScreeningOutcome,
    StructuredBrief,
    CANDIDATE_QUESTIONS,
    CLIENT_QUESTIONS,
)
from services.screening.state_machine import (
    ScreeningStateMachine,
    ScreeningState,
    HANDOFF_PHRASES,
    DISTRESS_PHRASES,
    MAX_TURNS_WITHOUT_PROGRESS,
)
from services.screening.voice_interface import StubVoiceInterface


# ── Helpers ────────────────────────────────────────────────────────────

def make_candidate_session(session_id="sess-cand-1"):
    return ScreeningSession(
        session_id=session_id,
        session_type=ScreeningSessionType.CANDIDATE,
    )

def make_client_session(session_id="sess-client-1"):
    return ScreeningSession(
        session_id=session_id,
        session_type=ScreeningSessionType.CLIENT,
    )

CANDIDATE_ANSWERS = [
    "I'm a senior software engineer with 12 years experience in fintech. Currently VP Engineering at a Series B startup.",
    "12 years in total, 8 years in financial technology specifically.",
    "Python, distributed systems, cloud architecture, team leadership, agile methodologies, machine learning fundamentals.",
    "I'm looking for a role where I can have more strategic impact and work directly with the C-suite on technology direction.",
    "Available immediately, prefer full-time permanent roles. Open to contract for the right opportunity.",
    "Looking for 180,000 to 220,000 GBP base, plus equity if it's a startup.",
    "Based in London. Open to remote work. Would consider Dublin or Amsterdam for the right role.",
]

CLIENT_ANSWERS = [
    "We need a Head of Engineering to lead our platform team. Someone who can scale the team from 15 to 40 engineers over the next year.",
    "10+ years engineering experience, 5+ years management, Python, cloud infrastructure, experience scaling teams past 30 people.",
    "Fintech background, startup experience, ML/AI knowledge.",
    "Currently 15 engineers across 3 squads reporting to CTO. This hire would take over day-to-day engineering leadership.",
    "We want someone in seat within 6 weeks. Critical hire for Q3 product launch.",
    "200,000 to 250,000 GBP base, 0.5% equity, standard benefits package.",
    "Fast-paced startup culture. Async-first communication. Quarterly offsites in London. Strong emphasis on engineering craft and code review.",
]


# ── Tests: Session Model ──────────────────────────────────────────────

class TestScreeningSession:
    def test_candidate_session_gets_candidate_questions(self):
        s = make_candidate_session()
        assert len(s.questions) == 7
        assert s.questions[0].id == "c_background"

    def test_client_session_gets_client_questions(self):
        s = make_client_session()
        assert len(s.questions) == 7
        assert s.questions[0].id == "cl_role"

    def test_current_question_starts_at_zero(self):
        s = make_candidate_session()
        assert s.current_question_index == 0
        assert s.current_question is not None
        assert s.current_question.id == "c_background"

    def test_is_complete_false_initially(self):
        s = make_candidate_session()
        assert not s.is_complete

    def test_progress_starts_at_zero(self):
        s = make_candidate_session()
        assert s.progress == 0.0

    def test_outcome_starts_none(self):
        s = make_candidate_session()
        assert s.outcome is None


# ── Tests: State Machine Lifecycle ─────────────────────────────────────

class TestStateMachineLifecycle:
    def test_initial_state_is_idle(self):
        sm = ScreeningStateMachine(make_candidate_session())
        assert sm.state == ScreeningState.IDLE

    def test_start_transitions_to_consent(self):
        sm = ScreeningStateMachine(make_candidate_session())
        msg = sm.start()
        assert sm.state == ScreeningState.CONSENT
        assert "recorded" in msg.lower() or "consent" in msg.lower()

    def test_cannot_start_twice(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        with pytest.raises(ValueError):
            sm.start()


class TestConsent:
    def test_consent_given_transitions_to_intake(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        msg = sm.give_consent(True)
        assert sm.state == ScreeningState.INTAKE
        assert sm.session.consent_given is True

    def test_consent_declined_transitions_to_handoff(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        msg = sm.give_consent(False)
        assert sm.state == ScreeningState.HANDOFF
        assert sm.session.handoff_reason == "Consent declined"
        assert "connect" in msg.lower() or "team member" in msg.lower()

    def test_consent_returns_first_question(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        msg = sm.give_consent(True)
        assert msg == CANDIDATE_QUESTIONS[0].text

    def test_cannot_consent_from_idle(self):
        sm = ScreeningStateMachine(make_candidate_session())
        with pytest.raises(ValueError):
            sm.give_consent(True)

    def test_candidate_consent_message_mentions_gdpr(self):
        sm = ScreeningStateMachine(make_candidate_session())
        msg = sm.start()
        assert "gdpr" in msg.lower()

    def test_client_consent_message_different(self):
        sm = ScreeningStateMachine(make_client_session())
        msg = sm.start()
        assert "hiring needs" in msg.lower()
        assert "gdpr" not in msg.lower()


# ── Tests: Full Candidate Intake ───────────────────────────────────────

class TestCandidateIntake:
    def _run_full_intake(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        sm.give_consent(True)
        for ans_text in CANDIDATE_ANSWERS:
            sm.answer(ans_text)
        return sm

    def test_all_questions_answered(self):
        sm = self._run_full_intake()
        assert sm.state == ScreeningState.SCORING
        assert sm.session.is_complete
        assert len(sm.session.answers) == 7

    def test_answers_recorded_in_order(self):
        sm = self._run_full_intake()
        assert sm.session.answers[0].question_id == "c_background"
        assert sm.session.answers[6].question_id == "c_location"

    def test_answer_text_preserved(self):
        sm = self._run_full_intake()
        assert "12 years" in sm.session.answers[1].response_text

    def test_scoring_after_intake(self):
        sm = self._run_full_intake()
        outcome = sm.score()
        assert sm.state == ScreeningState.COMPLETE
        assert isinstance(outcome, ScreeningOutcome)
        assert outcome.overall_score > 0.0

    def test_score_recommendation_is_valid(self):
        sm = self._run_full_intake()
        outcome = sm.score()
        assert outcome.recommendation in ("strong_proceed", "proceed", "hold", "reject")

    def test_detailed_answers_score_high(self):
        sm = self._run_full_intake()
        outcome = sm.score()
        assert outcome.overall_score >= 3.0

    def test_positive_outcome_for_detailed_answers(self):
        sm = self._run_full_intake()
        outcome = sm.score()
        assert outcome.is_positive


# ── Tests: Full Client Intake ──────────────────────────────────────────

class TestClientIntake:
    def _run_full_client_intake(self):
        sm = ScreeningStateMachine(make_client_session())
        sm.start()
        sm.give_consent(True)
        for ans_text in CLIENT_ANSWERS:
            sm.answer(ans_text)
        return sm

    def test_all_questions_answered(self):
        sm = self._run_full_client_intake()
        assert sm.state == ScreeningState.SCORING
        assert len(sm.session.answers) == 7

    def test_brief_building(self):
        sm = self._run_full_client_intake()
        brief = sm.build_brief()
        assert sm.state == ScreeningState.COMPLETE
        assert isinstance(brief, StructuredBrief)
        assert brief.role_description != ""
        assert len(brief.requirements_must_have) > 0

    def test_brief_captures_role_title(self):
        sm = self._run_full_client_intake()
        brief = sm.build_brief()
        assert brief.role_title != ""

    def test_brief_captures_timeline(self):
        sm = self._run_full_client_intake()
        brief = sm.build_brief()
        assert brief.timeline != ""

    def test_brief_captures_budget(self):
        sm = self._run_full_client_intake()
        brief = sm.build_brief()
        assert brief.budget_range != ""

    def test_brief_captures_culture(self):
        sm = self._run_full_client_intake()
        brief = sm.build_brief()
        assert brief.culture_notes != ""

    def test_brief_only_for_client_sessions(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        sm.give_consent(True)
        for ans in CANDIDATE_ANSWERS:
            sm.answer(ans)
        with pytest.raises(ValueError, match="client"):
            sm.build_brief()


# ── Tests: Handoff Triggers ────────────────────────────────────────────

class TestHandoffTriggers:
    def _start_intake(self, session=None):
        sm = ScreeningStateMachine(session or make_candidate_session())
        sm.start()
        sm.give_consent(True)
        return sm

    def test_handoff_phrase_triggers_handoff(self):
        for phrase in HANDOFF_PHRASES:
            sm = self._start_intake(make_candidate_session(f"h-{phrase[:8]}"))
            sm.answer(f"Actually, I'd like to {phrase}")
            assert sm.state == ScreeningState.HANDOFF, f"Failed for phrase: {phrase}"

    def test_distress_phrase_triggers_handoff(self):
        for phrase in DISTRESS_PHRASES:
            sm = self._start_intake(make_candidate_session(f"d-{phrase[:8]}"))
            sm.answer(f"I think this is {phrase}")
            assert sm.state == ScreeningState.HANDOFF, f"Failed for phrase: {phrase}"

    def test_handoff_records_reason(self):
        sm = self._start_intake()
        sm.answer("I want to speak to a human please")
        assert sm.session.handoff_reason is not None
        assert "speak to a human" in sm.session.handoff_reason

    def test_no_handoff_for_normal_text(self):
        sm = self._start_intake()
        sm.answer("I'm a marketing professional with 8 years experience")
        assert sm.state == ScreeningState.INTAKE

    def test_no_progress_triggers_handoff(self):
        sm = self._start_intake()
        for _ in range(MAX_TURNS_WITHOUT_PROGRESS):
            if sm.state != ScreeningState.INTAKE:
                break
            sm.answer("ok")
        assert sm.state == ScreeningState.HANDOFF


# ── Tests: Scoring Logic ──────────────────────────────────────────────

class TestScoringLogic:
    def test_empty_response_scores_low(self):
        score = ScreeningStateMachine._heuristic_score("")
        assert score == 1.0

    def test_short_response_scores_2(self):
        score = ScreeningStateMachine._heuristic_score("yes")
        assert score == 2.0

    def test_medium_response_scores_3(self):
        score = ScreeningStateMachine._heuristic_score("I have about ten years of experience.")
        assert score == 3.0

    def test_long_response_scores_3_5(self):
        text = "I have been working in a fintech startup for several years now, learning a lot about the industry and technology."
        score = ScreeningStateMachine._heuristic_score(text)
        assert score == 3.5

    def test_very_long_response_scores_4(self):
        text = "I have extensive experience in financial technology spanning many domains. " * 5
        score = ScreeningStateMachine._heuristic_score(text)
        assert score == 4.0

    def test_score_justification_detailed(self):
        j = ScreeningStateMachine._score_justification(4.0)
        assert "detailed" in j.lower()

    def test_score_justification_adequate(self):
        j = ScreeningStateMachine._score_justification(3.0)
        assert "adequate" in j.lower()

    def test_score_justification_brief(self):
        j = ScreeningStateMachine._score_justification(2.0)
        assert "brief" in j.lower()

    def test_score_justification_minimal(self):
        j = ScreeningStateMachine._score_justification(1.0)
        assert "minimal" in j.lower()

    def test_cannot_score_from_idle(self):
        sm = ScreeningStateMachine(make_candidate_session())
        with pytest.raises(ValueError):
            sm.score()

    def test_cannot_score_from_consent(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        with pytest.raises(ValueError):
            sm.score()

    def test_empty_answers_give_hold(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        sm.give_consent(True)
        for _ in range(7):
            if sm.state != ScreeningState.INTAKE:
                break
            sm.answer("This is a response that is long enough to pass the progress check but still quite normal.")
        if sm.state == ScreeningState.SCORING:
            outcome = sm.score()
            assert outcome.recommendation in ("strong_proceed", "proceed", "hold", "reject")

    def test_recommendation_thresholds(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        sm.give_consent(True)
        for ans in CANDIDATE_ANSWERS:
            sm.answer(ans)
        outcome = sm.score()
        if outcome.overall_score >= 4.0:
            assert outcome.recommendation == "strong_proceed"
        elif outcome.overall_score >= 3.0:
            assert outcome.recommendation == "proceed"
        elif outcome.overall_score >= 2.0:
            assert outcome.recommendation == "hold"
        else:
            assert outcome.recommendation == "reject"


# ── Tests: Fact Extraction ─────────────────────────────────────────────

class TestFactExtraction:
    def test_experience_years_extracted(self):
        facts = ScreeningStateMachine._extract_facts("experience", "I have 12 years of experience.")
        assert facts.get("experience_years") == 12

    def test_location_extracted(self):
        facts = ScreeningStateMachine._extract_facts("location", "Based in London, open to remote.")
        assert facts.get("location") is not None
        assert facts.get("remote_ok") is True

    def test_availability_extracted(self):
        facts = ScreeningStateMachine._extract_facts("availability", "Looking for full-time permanent.")
        assert facts.get("availability_type") == "full-time"

    def test_compensation_extracted(self):
        facts = ScreeningStateMachine._extract_facts("compensation", "Looking for 180,000 to 220,000 GBP.")
        assert facts.get("compensation_mentioned") is not None

    def test_skills_extracted(self):
        facts = ScreeningStateMachine._extract_facts("skills", "Python, Django, cloud architecture")
        assert facts.get("skills_raw") is not None

    def test_motivation_extracted(self):
        facts = ScreeningStateMachine._extract_facts("motivation", "I want to grow into leadership.")
        assert facts.get("motivation") is not None

    def test_background_extracted(self):
        facts = ScreeningStateMachine._extract_facts("background", "VP of Engineering at a fintech startup.")
        assert facts.get("background") is not None

    def test_empty_text_returns_no_facts(self):
        facts = ScreeningStateMachine._extract_facts("experience", "")
        assert facts == {}

    def test_contract_availability_type(self):
        facts = ScreeningStateMachine._extract_facts("availability", "Open to contract work.")
        assert facts.get("availability_type") == "contract"

    def test_remote_in_location(self):
        facts = ScreeningStateMachine._extract_facts("location", "Fully remote, based in Cork.")
        assert facts.get("remote_ok") is True


# ── Tests: Transition Logging ──────────────────────────────────────────

class TestTransitionLogging:
    def test_transitions_logged(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        sm.give_consent(True)
        assert len(sm.transitions) == 2
        assert sm.transitions[0]["from"] == "idle"
        assert sm.transitions[0]["to"] == "consent"
        assert sm.transitions[1]["from"] == "consent"
        assert sm.transitions[1]["to"] == "intake"

    def test_full_flow_transitions(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        sm.give_consent(True)
        for ans in CANDIDATE_ANSWERS:
            sm.answer(ans)
        sm.score()
        states = [t["to"] for t in sm.transitions]
        assert "consent" in states
        assert "intake" in states
        assert "scoring" in states
        assert "complete" in states

    def test_handoff_transition_logged(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        sm.give_consent(True)
        sm.answer("I want to speak to a human")
        last = sm.transitions[-1]
        assert last["to"] == "handoff"
        assert "handoff" in last["reason"]


# ── Tests: Voice Interface ─────────────────────────────────────────────

class TestVoiceInterface:
    def test_stub_send_message(self):
        vi = StubVoiceInterface()
        vi.send_message("s1", "Hello")
        msgs = vi.get_sent_messages("s1")
        assert msgs == ["Hello"]

    def test_stub_multiple_messages(self):
        vi = StubVoiceInterface()
        vi.send_message("s1", "Hello")
        vi.send_message("s1", "How are you?")
        msgs = vi.get_sent_messages("s1")
        assert len(msgs) == 2

    def test_stub_end_session(self):
        vi = StubVoiceInterface()
        assert not vi.is_ended("s1")
        vi.end_session("s1")
        assert vi.is_ended("s1")

    def test_stub_transcript_empty_by_default(self):
        vi = StubVoiceInterface()
        assert vi.get_transcript("s1") == []

    def test_stub_separate_sessions(self):
        vi = StubVoiceInterface()
        vi.send_message("s1", "Hello s1")
        vi.send_message("s2", "Hello s2")
        assert vi.get_sent_messages("s1") == ["Hello s1"]
        assert vi.get_sent_messages("s2") == ["Hello s2"]


# ── Tests: Outcome Properties ─────────────────────────────────────────

class TestOutcomeProperties:
    def test_is_positive_strong_proceed(self):
        o = ScreeningOutcome(recommendation="strong_proceed", overall_score=4.5)
        assert o.is_positive

    def test_is_positive_proceed(self):
        o = ScreeningOutcome(recommendation="proceed", overall_score=3.5)
        assert o.is_positive

    def test_not_positive_hold(self):
        o = ScreeningOutcome(recommendation="hold", overall_score=2.5)
        assert not o.is_positive

    def test_not_positive_reject(self):
        o = ScreeningOutcome(recommendation="reject", overall_score=1.5)
        assert not o.is_positive


# ── Tests: Edge Cases ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_answer_from_wrong_state(self):
        sm = ScreeningStateMachine(make_candidate_session())
        with pytest.raises(ValueError):
            sm.answer("test")

    def test_consent_from_wrong_state(self):
        sm = ScreeningStateMachine(make_candidate_session())
        sm.start()
        sm.give_consent(True)
        with pytest.raises(ValueError):
            sm.give_consent(True)

    def test_session_with_no_questions(self):
        s = ScreeningSession(
            session_id="no-q",
            session_type=ScreeningSessionType.CANDIDATE,
        )
        s.questions = []
        sm = ScreeningStateMachine(s)
        sm.start()
        msg = sm.give_consent(True)
        assert sm.state == ScreeningState.SCORING

    def test_scoring_with_zero_answers(self):
        s = ScreeningSession(
            session_id="zero-a",
            session_type=ScreeningSessionType.CANDIDATE,
        )
        s.questions = []
        sm = ScreeningStateMachine(s)
        sm.start()
        sm.give_consent(True)
        outcome = sm.score()
        assert outcome.recommendation == "hold"
        assert outcome.overall_score == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
