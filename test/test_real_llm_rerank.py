"""
THE PROOF — ainm Search's AI fires end-to-end in a deployed-shape environment.

This makes ONE real Anthropic API call through the full production path:
    services.ai.agent_service.rerank_matches
      -> agentic_core AnthropicClient (real key)
      -> agentic_core MatchReRankAgent.run  (real LLM)
and asserts the result is non-empty, well-formed, carries a real generated
rationale, and was actually billed (cost_usd > 0) — which a mock cannot fake.

Gated: SKIPS unless a real ANTHROPIC_API_KEY is present, so CI stays hermetic.
Run it for real with:
    EXECFLEX_AI_MATCH_RERANK=1 ANTHROPIC_API_KEY=sk-ant-... \
      .venv/Scripts/python.exe -m pytest test/test_real_llm_rerank.py -v -s
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="requires a real ANTHROPIC_API_KEY — this is the live end-to-end proof",
)

JOB = {
    "title": "Senior Python Engineer",
    "description": "Own our data platform. Python, SQL, and AWS in a fintech team.",
    "location": "Dublin, Ireland",
    "industry": "Technology",
    "skills": ["Python", "SQL", "AWS"],
}

CANDIDATES = [
    {
        "candidate_id": "cand-strong",
        "name": "Aoife Brennan",
        "headline": "Senior Data Engineer, 8y Python/AWS",
        "skills": ["Python", "SQL", "AWS", "Airflow"],
        "experience_years": 8,
        "composite_score": 88.0,
    },
    {
        "candidate_id": "cand-mid",
        "name": "Cian Murphy",
        "headline": "Backend Engineer, Python + Postgres",
        "skills": ["Python", "SQL"],
        "experience_years": 5,
        "composite_score": 71.0,
    },
    {
        "candidate_id": "cand-weak",
        "name": "Sean Doyle",
        "headline": "Junior Frontend Developer",
        "skills": ["JavaScript", "CSS"],
        "experience_years": 1,
        "composite_score": 34.0,
    },
]


def test_real_llm_rerank_end_to_end():
    # Flag ON, in-process, prod path.
    os.environ["EXECFLEX_AI_MATCH_RERANK"] = "1"

    import services.ai.agent_service as svc
    svc._llm_client = None  # force a fresh real client from the env key

    result = svc.rerank_matches(job=JOB, candidates=CANDIDATES)

    # 1. It actually ran — None would mean flag-off, no key, import error, or a
    #    swallowed exception. A real call returns a dict.
    assert result is not None, (
        "rerank_matches returned None — the AI path did NOT fire. "
        "Check the key, the flag, and that agentic-core imported."
    )

    # 2. Well-formed, and self-declared AI output.
    assert result.get("ai_generated") is True
    assert isinstance(result.get("reranked"), list) and len(result["reranked"]) >= 1

    # 3. A REAL, non-empty generated rationale (the thing a mock would not produce).
    summary = result.get("reasoning_summary")
    assert isinstance(summary, str) and len(summary.strip()) > 20, (
        f"expected a substantive generated rationale, got: {summary!r}"
    )

    # 4. It was actually billed — proof of a live API call, not a stub.
    cost = result.get("cost_usd")
    assert cost is not None and cost > 0, (
        f"cost_usd should be > 0 for a real API call, got {cost!r}"
    )

    assert result.get("confidence")

    # Visible proof when run with -s.
    print("\n--- REAL LLM RE-RANK PROOF ---")
    print("confidence:", result["confidence"], "| cost_usd:", cost)
    print("reasoning_summary:", summary)
    for r in result["reranked"]:
        print("  ranked:", r)
    print("--- END PROOF ---")
