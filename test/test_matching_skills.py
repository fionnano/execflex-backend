"""
Skill matching is a real, load-bearing part of the composite score.

Context: the matching engine already factors skills via _score_skills at the
"skills_fit" weight (0.25) inside MatchEngine.score_candidate — there is no
separate _calc_weighted_score function. These tests LOCK that behaviour so the
claim "ainm Search matches on skills" is true AND verified: a candidate whose
skills match the role's required skills must out-score an otherwise-identical
candidate whose skills do not.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.matching.models import Candidate, Role
from services.matching.engine import MatchEngine, DEFAULT_WEIGHTS


def _role() -> Role:
    return Role(
        id="role-1",
        title="Senior Python Engineer",
        industry="technology",
        required_skills={"python", "sql", "aws"},
        min_experience=5,
        location="Dublin",
        commitment_type="permanent",
        budget_min=80000,
        budget_max=120000,
    )


def _candidate(cid: str, skills: set) -> Candidate:
    """Identical candidates except for the skills set."""
    return Candidate(
        id=cid,
        name=cid,
        headline="Engineer",  # no skill tokens, so headline fallback can't leak a match
        industries={"technology"},
        skills=skills,
        experience_years=7,
        location="Dublin",
        availability="immediate",
        compensation_min=90000,
        compensation_max=110000,
        open_to_opportunities="yes",
        screening_recommendation="advance",
        screening_score=80.0,
    )


def test_skill_matched_candidate_scores_higher_than_skill_blind():
    engine = MatchEngine()
    role = _role()

    matched = _candidate("matched", skills={"python", "sql", "aws"})
    blind = _candidate("blind", skills={"cooking", "gardening", "welding"})

    matched_result = engine.score_candidate(matched, role)
    blind_result = engine.score_candidate(blind, role)

    # The ONLY difference between the two is skills, so composite must differ
    # and the skill-matched candidate must win.
    assert matched_result.score > blind_result.score, (
        f"skill-matched ({matched_result.score}) should beat "
        f"skill-blind ({blind_result.score})"
    )

    # The gap must come from the skills_fit dimension specifically.
    matched_skills = matched_result.explanation.dimension_scores["skills_fit"].score
    blind_skills = blind_result.explanation.dimension_scores["skills_fit"].score
    assert matched_skills == 100.0
    assert blind_skills == 0.0

    # And the composite gap should equal the skills weight applied to the
    # dimension gap (all other dimensions identical): 0.25 * (100 - 0) = 25.
    expected_gap = DEFAULT_WEIGHTS["skills_fit"] * (matched_skills - blind_skills)
    actual_gap = matched_result.score - blind_result.score
    assert abs(actual_gap - expected_gap) < 0.5, (
        f"composite gap {actual_gap} should equal skills-weighted gap {expected_gap}"
    )


def test_partial_skill_overlap_scores_between_full_and_none():
    engine = MatchEngine()
    role = _role()

    full = engine.score_candidate(_candidate("full", {"python", "sql", "aws"}), role)
    partial = engine.score_candidate(_candidate("partial", {"python"}), role)
    none = engine.score_candidate(_candidate("none", {"welding"}), role)

    assert full.score > partial.score > none.score
    # partial = 1 of 3 required skills -> ~33 on the dimension
    assert 30 <= partial.explanation.dimension_scores["skills_fit"].score <= 40


def test_ranking_orders_by_skill_match_when_all_else_equal():
    engine = MatchEngine()
    role = _role()
    candidates = [
        _candidate("blind", {"welding"}),
        _candidate("matched", {"python", "sql", "aws"}),
        _candidate("partial", {"python", "sql"}),
    ]
    results = engine.match(candidates, role, limit=10)
    ranked_ids = [r.candidate.id for r in results]
    assert ranked_ids[0] == "matched", f"expected skill-matched first, got {ranked_ids}"
    assert ranked_ids.index("partial") < ranked_ids.index("blind")
