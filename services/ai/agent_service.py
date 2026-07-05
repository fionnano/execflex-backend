"""Thin service layer over agentic-core recruitment agents.

Each function checks the feature flag, builds the agent, runs it,
and returns the result. If the flag is off, returns None — callers
fall through to deterministic-only behaviour.

LLMClient is constructed lazily on first use. The ANTHROPIC_API_KEY
env var must be set for LLM calls to work.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from services.ai.feature_flags import (
    match_rerank_enabled,
    screening_summary_enabled,
    cv_parser_enabled,
    jd_generator_enabled,
    question_flow_enabled,
    compliance_check_enabled,
)

logger = logging.getLogger("execflex.ai.agent_service")

_llm_client = None


def _get_llm_client():
    """Lazy-init the Anthropic LLM client."""
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — AI agents will not function")
        return None

    try:
        from agentic_core.primitives.llm.anthropic_client import AnthropicLLMClient
        _llm_client = AnthropicLLMClient(api_key=api_key)
        return _llm_client
    except ImportError:
        logger.error("agentic-core not installed — AI agents unavailable")
        return None


def rerank_matches(
    job: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """LLM re-rank match results. Returns None if flag off or error."""
    if not match_rerank_enabled():
        return None

    client = _get_llm_client()
    if client is None:
        return None

    try:
        from agentic_core.agents.recruitment import MatchReRankAgent
        agent = MatchReRankAgent(client)
        result = agent.run(job=job, candidates=candidates)
        logger.info(
            "Match re-rank: confidence=%s cost=$%.4f",
            result.confidence.level,
            result.cost_usd or 0,
        )
        return {
            "reranked": result.reranked,
            "reasoning_summary": result.reasoning_summary,
            "confidence": result.confidence.level,
            "needs_review": result.needs_review,
            "cost_usd": result.cost_usd,
            "ai_generated": True,
        }
    except Exception:
        logger.exception("Match re-rank agent failed")
        return None


def summarise_screening(
    candidate_name: str,
    role_title: str,
    transcript: list[dict[str, str]],
    role_requirements: str | None = None,
    screening_score: float | None = None,
) -> dict[str, Any] | None:
    """LLM screening summary. Returns None if flag off or error."""
    if not screening_summary_enabled():
        return None

    client = _get_llm_client()
    if client is None:
        return None

    try:
        from agentic_core.agents.recruitment import ScreeningSummaryAgent
        agent = ScreeningSummaryAgent(client)
        result = agent.run(
            candidate_name=candidate_name,
            role_title=role_title,
            transcript=transcript,
            role_requirements=role_requirements,
            screening_score=screening_score,
        )
        logger.info(
            "Screening summary: next_step=%s confidence=%s cost=$%.4f",
            result.next_step,
            result.confidence.level,
            result.cost_usd or 0,
        )
        return {
            "strengths": result.strengths,
            "gaps": result.gaps,
            "flags": result.flags,
            "next_step": result.next_step,
            "next_step_rationale": result.next_step_rationale,
            "one_line_summary": result.one_line_summary,
            "confidence": result.confidence.level,
            "needs_review": result.needs_review,
            "cost_usd": result.cost_usd,
            "ai_generated": True,
        }
    except Exception:
        logger.exception("Screening summary agent failed")
        return None


def parse_cv(
    cv_text: str,
    target_role: str | None = None,
) -> dict[str, Any] | None:
    """LLM CV parsing. Returns None if flag off or error."""
    if not cv_parser_enabled():
        return None

    client = _get_llm_client()
    if client is None:
        return None

    try:
        from agentic_core.agents.recruitment import CVParserAgent
        agent = CVParserAgent(client)
        result = agent.run(cv_text=cv_text, target_role=target_role)
        logger.info(
            "CV parsed: name=%s skills=%d confidence=%s cost=$%.4f",
            result.full_name or "unknown",
            len(result.skills),
            result.confidence.level,
            result.cost_usd or 0,
        )
        return {
            "profile": result.profile,
            "confidence": result.confidence.level,
            "needs_review": result.needs_review,
            "cost_usd": result.cost_usd,
            "ai_generated": True,
        }
    except Exception:
        logger.exception("CV parser agent failed")
        return None


def generate_jd(
    role_title: str,
    company_summary: str,
    responsibilities: str,
    requirements: str,
    pay_range_min: float,
    pay_range_max: float,
    pay_currency: str,
    location: str,
    **optional_fields: Any,
) -> dict[str, Any] | None:
    """LLM JD generation. Returns None if flag off or error."""
    if not jd_generator_enabled():
        return None

    client = _get_llm_client()
    if client is None:
        return None

    try:
        from agentic_core.agents.recruitment import JDGeneratorAgent
        agent = JDGeneratorAgent(client)
        result = agent.run(
            role_title=role_title,
            company_summary=company_summary,
            responsibilities=responsibilities,
            requirements=requirements,
            pay_range_min=pay_range_min,
            pay_range_max=pay_range_max,
            pay_currency=pay_currency,
            location=location,
            **{k: v for k, v in optional_fields.items() if v is not None},
        )
        logger.info(
            "JD generated: role=%s words=%d gender_neutral=%s confidence=%s cost=$%.4f",
            role_title,
            result.word_count,
            result.confidence.gender_neutral_passed,
            result.confidence.level,
            result.cost_usd or 0,
        )
        return {
            "posting_text": result.posting_text,
            "gender_neutral_flags": result.gender_neutral_flags,
            "word_count": result.word_count,
            "confidence": result.confidence.level,
            "needs_review": result.needs_review,
            "cost_usd": result.cost_usd,
            "ai_generated": True,
        }
    except Exception:
        logger.exception("JD generator agent failed")
        return None


def get_question_flow_data(role_type: str) -> dict[str, Any] | None:
    """Get per-role question flow. Returns None if flag off."""
    if not question_flow_enabled():
        return None

    try:
        from agentic_core.agents.recruitment import get_question_flow
        flow = get_question_flow(role_type)
        return flow.to_dict()
    except Exception:
        logger.exception("Question flow lookup failed")
        return None


def check_prohibited_practices(
    answers: dict[str, str],
) -> dict[str, Any] | None:
    """Check answers against EU AI Act Article 5 prohibited practices.

    Pure logic — no LLM call. Returns None if flag off.
    """
    if not compliance_check_enabled():
        return None

    try:
        from agentic_core.agents.compliance import check_prohibited_practices as run_check
        result = run_check(answers)
        logger.info(
            "Prohibited practices check: hard_stop=%s prohibited=%s high_risk=%s flags=%d",
            result.has_hard_stop,
            result.has_prohibited,
            result.has_high_risk,
            len(result.flags),
        )
        return result.to_dict()
    except Exception:
        logger.exception("Prohibited practices check failed")
        return None


def snapshot_score(
    *,
    uses_ai: str,
    business_functions: list[str] | None = None,
    affects_people: str = "no",
    in_eu: str = "no",
    has_documentation: str = "no",
) -> dict[str, Any] | None:
    """Deterministic snapshot risk score. No LLM call. Returns None if flag off."""
    if not compliance_check_enabled():
        return None

    try:
        from agentic_core.agents.compliance import calculate_snapshot_score
        result = calculate_snapshot_score(
            uses_ai=uses_ai,
            business_functions=business_functions,
            affects_people=affects_people,
            in_eu=in_eu,
            has_documentation=has_documentation,
        )
        logger.info(
            "Snapshot score: score=%d risk=%s colour=%s",
            result.score,
            result.risk_level,
            result.colour,
        )
        return result.to_dict()
    except Exception:
        logger.exception("Snapshot scoring failed")
        return None


def snapshot_gaps(
    *,
    uses_ai: str,
    business_functions: list[str] | None = None,
    affects_people: str = "no",
    in_eu: str = "no",
    has_documentation: str = "no",
) -> dict[str, Any] | None:
    """LLM-generated gap analysis from snapshot answers. Returns None if flag off."""
    if not compliance_check_enabled():
        return None

    client = _get_llm_client()
    if client is None:
        return None

    try:
        from agentic_core.agents.compliance import SnapshotGapsAgent
        agent = SnapshotGapsAgent(client)
        result = agent.run(
            uses_ai=uses_ai,
            business_functions=business_functions,
            affects_people=affects_people,
            in_eu=in_eu,
            has_documentation=has_documentation,
        )
        logger.info(
            "Snapshot gaps: count=%d confidence=%s cost=$%.4f",
            len(result.gaps),
            result.confidence.level,
            result.cost_usd or 0,
        )
        return result.to_dict()
    except Exception:
        logger.exception("Snapshot gaps agent failed")
        return None
