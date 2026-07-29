"""Fix 1(c) + Fix 5: prove a posted job's skills flow into the matcher.

The launch bug was that POST /jobs never persisted skills where matches.py reads
them (metadata.required_skills), so matching was skill-blind. This test exercises
the exact seam that was broken: jobs._build_metadata (what create_job stores) →
the Role matches.py constructs from job_data["metadata"] → the MatchEngine score.
A skill-matched candidate must outscore a skill-blind one for a real posted job.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routes.api_v1.jobs import _build_metadata, _normalize_commitment, _resolve_type
from services.matching.models import Candidate, Role
from services.matching.engine import MatchEngine


def _role_from_posted_job(post_body: dict) -> Role:
    """Mirror exactly what create_job stores and what matches.py then reads."""
    metadata = _build_metadata(post_body)  # what POST /jobs persists into opportunities.metadata
    # matches.py builds the Role from job_data:
    return Role(
        id="job-1",
        title=post_body.get("title", ""),
        industry=post_body.get("industry", ""),
        required_skills=set(metadata.get("required_skills", [])),
        min_experience=metadata.get("min_experience", 0),
        location=post_body.get("location", ""),
        commitment_type=_normalize_commitment(post_body.get("commitment_type")),
        budget_min=post_body.get("pay_range_min") or 0,
        budget_max=post_body.get("pay_range_max") or 0,
        is_ned=_resolve_type(post_body) == "hire_ned",
    )


def _candidate(cid, skills):
    return Candidate(id=cid, name=cid, skills=set(skills), experience_years=10,
                     location="Dublin")


POSTED_JOB = {
    "title": "Head of Data Engineering", "commitment_type": "full-time",
    "skills_required": ["Spark", "dbt", "Python"], "experience_min": 8,
    "location": "Dublin", "pay_range_min": 120000, "pay_range_max": 160000,
    "industry": "FinTech",
}


def test_posted_job_persists_skills_into_metadata():
    meta = _build_metadata(POSTED_JOB)
    assert meta["required_skills"] == ["Spark", "dbt", "Python"]
    assert meta["min_experience"] == 8


def test_skill_matched_candidate_outscores_skill_blind_for_a_real_job():
    role = _role_from_posted_job(POSTED_JOB)
    assert role.required_skills == {"spark", "dbt", "python"}, "job skills must reach the Role"

    engine = MatchEngine()
    matched = engine.score_candidate(_candidate("matched", ["Spark", "dbt", "Python"]), role)
    blind = engine.score_candidate(_candidate("blind", ["Cobol", "Fortran"]), role)

    assert matched.score > blind.score, (
        f"skill-matched ({matched.score}) must beat skill-blind ({blind.score})")
    assert matched.explanation.dimension_scores["skills_fit"].score == 100.0
    assert blind.explanation.dimension_scores["skills_fit"].score == 0.0


def test_commitment_and_type_are_dbvalid_for_a_posted_job():
    # The other half of the 500 fix: enum values that satisfy the DB constraints.
    assert _normalize_commitment("full-time") == "full_time"
    assert _normalize_commitment("interim") == "contract"
    assert _resolve_type({}) == "hire_fractional"
    assert _resolve_type({"is_ned": True}) == "hire_ned"
