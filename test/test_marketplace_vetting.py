"""Vetting engine — scoring-path tests with synthetic responses.

Covers the deterministic heuristic scorer, the AI path with a MOCKED LLM client
(no network), JSON extraction robustness, and the pass/fail threshold. Zero real
LLM calls, zero real data.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest

from services.marketplace.vetting import (
    score_vetting, question_set, _extract_json, _heuristic_answer_score,
    VettingResult,
)
from services.marketplace.constants import VETTING_PASS_THRESHOLD, VETTING_TRACKS


# ── Question sets ────────────────────────────────────────────────────

def test_question_set_has_technical_and_leadership_for_every_track():
    for track in VETTING_TRACKS:
        qs = question_set(track)
        assert len(qs) == 6, track
        comps = {q["competency"] for q in qs}
        assert "Leadership" in comps
        # weights sum to ~1.0
        assert abs(sum(q["weight"] for q in qs) - 1.0) < 1e-6, track


def test_unknown_track_falls_back_to_valid_set():
    qs = question_set("nonsense")
    assert len(qs) == 6


# ── Heuristic scorer ─────────────────────────────────────────────────

def _responses(answers, track="ml_platform"):
    qs = question_set(track)
    return [
        {"question_id": q["id"], "competency": q["competency"], "weight": q["weight"],
         "text": answers[i] if i < len(answers) else ""}
        for i, q in enumerate(qs)
    ]


STRONG_ANSWER = (
    "I led a team of 12 that redesigned our model serving stack, cutting p99 "
    "latency from 800ms to 120ms and reducing GPU cost by 40%. I owned the "
    "incident response when drift caused a 15% accuracy drop in production, "
    "shipped a rollback, and introduced automated drift detection with clear SLAs."
)
WEAK_ANSWER = "I think AI is important and I have done some stuff with data."


def test_strong_answers_pass_heuristic():
    # Force heuristic path.
    os.environ["MARKETPLACE_VETTING_AI"] = "off"
    res = score_vetting(leader_name="Test Leader", track="ml_platform",
                        responses=_responses([STRONG_ANSWER] * 6))
    assert isinstance(res, VettingResult)
    assert res.ai_generated is False
    assert res.score >= VETTING_PASS_THRESHOLD
    assert res.passed is True
    assert res.status == "verified"
    assert "Test Leader" in res.rationale
    assert len(res.per_competency) == 6


def test_weak_answers_fail_heuristic():
    os.environ["MARKETPLACE_VETTING_AI"] = "off"
    res = score_vetting(leader_name="Weak Leader", track="ai_product",
                        responses=_responses([WEAK_ANSWER] * 6))
    assert res.score < VETTING_PASS_THRESHOLD
    assert res.passed is False
    assert res.status == "rejected"
    assert res.flags  # a below-threshold flag is attached


def test_empty_answer_scores_zero():
    assert _heuristic_answer_score("") == 0


def test_quantified_answer_beats_vague_answer():
    assert _heuristic_answer_score(STRONG_ANSWER) > _heuristic_answer_score(WEAK_ANSWER)


def test_no_responses_is_safe_and_fails():
    os.environ["MARKETPLACE_VETTING_AI"] = "off"
    res = score_vetting(leader_name="Nobody", track="ml_platform", responses=[])
    assert res.score == 0
    assert res.passed is False


# ── AI path with a mocked client ─────────────────────────────────────

class _MockResp:
    def __init__(self, text):
        self.text = text


class _MockClient:
    """Returns a Haiku-style JSON array then a Sonnet-style JSON object."""
    def __init__(self, per_answer_score=88):
        self.per_answer_score = per_answer_score
        self.calls = []

    def complete(self, prompt, *, model=None, max_tokens=None, temperature=None, system=None):
        self.calls.append(model)
        if "JSON array" in prompt or "ANSWERS:" in prompt:
            # Haiku per-answer call. Echo a score for each question id present.
            ids = []
            # crude id extraction from the embedded JSON
            try:
                start = prompt.index("ANSWERS:") + len("ANSWERS:")
                items = json.loads(prompt[start:].strip())
                ids = [it["question_id"] for it in items]
            except Exception:
                ids = []
            arr = [{"question_id": i, "score": self.per_answer_score, "note": "solid"} for i in ids]
            return _MockResp(json.dumps(arr))
        # Sonnet rationale call.
        return _MockResp(json.dumps({
            "rationale": "Strong, quantified evidence across systems and leadership.",
            "confidence": "high", "flags": [],
        }))


def test_ai_path_uses_haiku_then_sonnet_and_passes():
    os.environ["MARKETPLACE_VETTING_AI"] = "on"
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")  # enables the AI branch
    client = _MockClient(per_answer_score=88)
    res = score_vetting(leader_name="AI Leader", track="ml_platform",
                        responses=_responses([STRONG_ANSWER] * 6), client=client)
    assert res.ai_generated is True
    assert res.score == 88
    assert res.passed is True
    assert res.confidence == "high"
    # Haiku model routed first, Sonnet second.
    assert any("haiku" in (m or "") for m in client.calls)
    assert any("sonnet" in (m or "") for m in client.calls)


def test_ai_path_low_scores_reject():
    os.environ["MARKETPLACE_VETTING_AI"] = "on"
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
    client = _MockClient(per_answer_score=40)
    res = score_vetting(leader_name="Low Leader", track="data_engineering",
                        responses=_responses([WEAK_ANSWER] * 6), client=client)
    assert res.score == 40
    assert res.passed is False
    assert res.status == "rejected"


def test_ai_failure_falls_back_to_heuristic():
    os.environ["MARKETPLACE_VETTING_AI"] = "on"
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

    class _Boom:
        def complete(self, *a, **k):
            raise RuntimeError("network down")

    res = score_vetting(leader_name="Fallback Leader", track="ml_platform",
                        responses=_responses([STRONG_ANSWER] * 6), client=_Boom())
    # Fell back to heuristic — still a valid result, not AI-generated.
    assert res.ai_generated is False
    assert isinstance(res.score, int)


# ── JSON extraction robustness ───────────────────────────────────────

def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_embedded_array():
    assert _extract_json('Here you go: [{"x": 2}] thanks') == [{"x": 2}]


def test_extract_json_garbage_returns_none():
    assert _extract_json("no json here") is None


def test_to_dict_includes_threshold():
    os.environ["MARKETPLACE_VETTING_AI"] = "off"
    res = score_vetting(leader_name="X", track="ml_platform",
                        responses=_responses([STRONG_ANSWER] * 6))
    d = res.to_dict()
    assert d["threshold"] == VETTING_PASS_THRESHOLD
    assert set(["score", "passed", "status", "rationale", "per_competency"]).issubset(d)
