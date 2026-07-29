"""AI Act assessment engine — orchestrates the agentic-core compliance module.

Deterministic backbone (always, token-free):
    prohibited-practice screen (Article 5)  → hard-stop gating
    snapshot scorer                         → readiness score 0-100
    risk classification                     → prohibited / high / limited / minimal
    obligation mapper                       → the Articles you're subject to
    deterministic gap analysis              → what's missing, per Article

Optional AI narrative (behind AIACT_AI or a per-request flag), ALWAYS marked as
AI-generated: the scoring_engine (Sonnet) writes an explainable summary and
recommendations. On any failure the deterministic narrative stands in — the demo
and tests never depend on live tokens.

The rule-based classification/obligations/gaps are NOT AI-generated (they are
explainable and deterministic); only the optional narrative summary and its
recommendations are, and they carry ai_generated=True.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from agentic_core.agents.compliance import (
    HIGH, LIMITED, MINIMAL, UNACCEPTABLE,
    calculate_snapshot_score, check_prohibited_practices, map_obligations,
    get_question_set,
)

logger = logging.getLogger("execflex.aiact.engine")

_HIGH_RISK_FUNCTIONS = {"hr", "finance", "legal_compliance"}


# ── Context derivation ───────────────────────────────────────────────────────

def _functions(answers: dict) -> list[str]:
    fns = answers.get("business_functions")
    if isinstance(fns, str):
        return [fns]
    return list(fns or [])


def _context(answers: dict) -> dict:
    fns = _functions(answers)
    return {
        "functions": fns,
        "employment": "hr" in fns,
        "high_area": any(f in _HIGH_RISK_FUNCTIONS for f in fns),
        "affects": answers.get("affects_people"),
        "auto_hiring": answers.get("automated_hiring_decisions"),
        "uses_biometric": answers.get("uses_biometric_data") == "yes",
        "uses_ai": answers.get("uses_ai"),
    }


def classify_risk(answers: dict, prohibited) -> str:
    """Deterministic EU AI Act risk-tier classification."""
    if prohibited.has_hard_stop or prohibited.has_prohibited:
        return UNACCEPTABLE
    ctx = _context(answers)
    if ctx["uses_ai"] == "no":
        return MINIMAL
    if prohibited.has_high_risk:  # biometric identification/categorisation
        return HIGH
    if ctx["auto_hiring"] == "yes":
        return HIGH
    if ctx["high_area"] and ctx["affects"] == "yes":
        return HIGH
    if ctx["affects"] in ("yes", "unsure"):
        return LIMITED
    return MINIMAL


# ── Deterministic gap analysis ───────────────────────────────────────────────

def deterministic_gaps(answers: dict, risk: str) -> list[str]:
    gaps: list[str] = []
    if answers.get("human_oversight") not in ("yes",):
        gaps.append("No meaningful human review of AI outputs before decisions are made "
                    "(Article 14 human oversight).")
    if answers.get("data_governance") not in ("yes",):
        gaps.append("No documented data-quality or bias controls for the system's inputs "
                    "(Article 10 data governance).")
    if answers.get("keeps_logs") != "yes":
        gaps.append("Decisions and system operation are not logged for traceability "
                    "(Article 12 record-keeping).")
    if answers.get("has_documentation") in ("no", None, ""):
        gaps.append("No technical documentation exists for the system "
                    "(Article 11 / Annex IV).")
    if answers.get("candidates_informed") not in ("yes",):
        gaps.append("People affected are not clearly informed that AI is used in the "
                    "decision (Articles 26(7) & 50 transparency).")
    if risk == HIGH and not gaps:
        gaps.append("High-risk classification requires a documented conformity assessment "
                    "before deployment (Articles 9 & 43).")
    return gaps[:6]


def _decision(risk: str, score: int, gaps: list[str]) -> str:
    if risk == UNACCEPTABLE:
        return "Significant gaps identified"
    if risk == HIGH:
        if score >= 80 and not gaps:
            return "Ready to proceed"
        return "Further review recommended" if score >= 50 else "Significant gaps identified"
    # Limited / minimal
    if score >= 65 and len(gaps) <= 1:
        return "Ready to proceed"
    return "Further review recommended" if score >= 40 else "Significant gaps identified"


def _snapshot_inputs(answers: dict) -> dict:
    return {
        "uses_ai": answers.get("uses_ai") or "unsure",
        "business_functions": _functions(answers),
        "affects_people": answers.get("affects_people") or "no",
        "in_eu": answers.get("in_eu") or "no",
        "has_documentation": answers.get("has_documentation") or "no",
    }


def _deterministic_rationale(system_name, risk, score, snapshot, obligations, prohibited) -> str:
    parts = [snapshot.summary]
    parts.append(obligations.headline)
    if prohibited.has_hard_stop or prohibited.has_prohibited:
        parts.insert(0, "One or more answers indicate a practice prohibited under EU AI "
                        "Act Article 5 — this is a hard stop.")
    return " ".join(p for p in parts if p)


# ── Public entry point ───────────────────────────────────────────────────────

def score_assessment(*, system_name: str, answers: dict,
                     use_ai: Optional[bool] = None) -> dict:
    """Run the full assessment and return an explainable result dict.

    Always produces a deterministic result; when AI is enabled and available,
    adds a marked AI-generated narrative summary and recommendations.
    """
    answers = answers or {}
    prohibited = check_prohibited_practices(answers)
    risk = classify_risk(answers, prohibited)

    snapshot = calculate_snapshot_score(**_snapshot_inputs(answers))
    # For a prohibited/high classification, don't let the light snapshot look green.
    score = int(snapshot.score)
    if risk == UNACCEPTABLE:
        score = min(score, 15)
    elif risk == HIGH:
        score = min(score, 60)

    ctx = _context(answers)
    obligations = map_obligations(
        risk_classification=risk,
        in_employment=ctx["employment"] or ctx["auto_hiring"] == "yes",
        uses_biometric=ctx["uses_biometric"],
        affects_people=ctx["affects"] == "yes",
    )
    gaps = deterministic_gaps(answers, risk)
    decision = _decision(risk, score, gaps)

    # Deterministic recommendations = the practical deployer actions for the top
    # obligations, plus the snapshot's recommendations.
    rec_from_obligations = [
        {"text": o.deployer_action, "article": o.article}
        for o in obligations.obligations[:5]
    ]
    articles = sorted({o.article for o in obligations.obligations})

    summary = _deterministic_rationale(system_name, risk, score, snapshot, obligations, prohibited)
    ai_generated = False
    model_used = "aiact_deterministic_v1"
    recommendations = rec_from_obligations

    if use_ai is None:
        use_ai = _ai_enabled()
    if use_ai:
        ai = _ai_narrative(system_name, answers, risk, score, prohibited)
        if ai:
            summary = ai["summary"]
            if ai.get("recommendations"):
                recommendations = ai["recommendations"]
            ai_generated = True
            model_used = ai.get("model_used", "aiact_ai_v1")

    return {
        "system_name": system_name,
        "risk_classification": risk,
        "readiness_score": score,
        "decision": decision,
        "summary": summary,
        "ai_generated": ai_generated,
        "model_used": model_used,
        "prohibited": prohibited.to_dict(),
        "obligations_headline": obligations.headline,
        "obligations": [o.to_dict() for o in obligations.obligations],
        "gaps": gaps,
        "recommendations": recommendations,
        "articles_referenced": articles,
        "snapshot": snapshot.to_dict(),
    }


# ── Optional AI narrative (scoring_engine, Sonnet) ───────────────────────────

def _ai_enabled() -> bool:
    if os.environ.get("AIACT_AI", "").lower() in ("1", "true", "on", "yes"):
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return False


def _grouped_answers(answers: dict) -> dict[str, dict]:
    """Group flat answers by the question set's stages for the scoring agent."""
    grouped: dict[str, dict] = {}
    stage_map = {"intake": "A", "prohibited": "B", "scope": "B2", "governance": "C"}
    for stage in get_question_set():
        key = stage_map.get(stage.id, stage.id)
        bucket = {}
        for q in stage.questions:
            if q.id in answers and answers[q.id] not in (None, "", []):
                bucket[q.id] = answers[q.id]
        if bucket:
            grouped[key] = bucket
    return grouped


def _ai_narrative(system_name, answers, risk, score, prohibited) -> Optional[dict]:
    """Use the agentic-core scoring_engine for an explainable AI narrative.

    Returns {summary, recommendations, model_used} or None on any failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from agentic_core.primitives.llm.anthropic_client import AnthropicClient
        from agentic_core.agents.compliance import ScoringEngineAgent
        client = AnthropicClient(api_key=api_key)
        agent = ScoringEngineAgent(client)
        result = agent.run(
            system_name=system_name or "the AI system",
            raw_answers=_grouped_answers(answers),
            prohibited_flags=prohibited.to_dict(),
        )
        recs = [{"text": r.text, "article": r.article} for r in result.recommendations]
        return {
            "summary": result.summary,
            "recommendations": recs or None,
            "model_used": "aiact_ai_v1 (agentic-core scoring_engine / sonnet)",
        }
    except Exception:
        logger.exception("AI Act narrative failed — using deterministic rationale")
        return None
