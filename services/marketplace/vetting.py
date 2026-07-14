"""Marketplace vetting engine — the moat.

A leader answers a fixed, track-specific set of technical + leadership questions.
The engine scores the responses and returns an explainable rationale plus a
pass/fail against a threshold. Passing sets vetting_status='verified' and earns
the "Independently vetted" badge.

Design (see DECISIONS.md D-16):
- Mechanical per-answer scoring is routed to Haiku; the overall reasoning +
  rationale is routed to Sonnet — matching agentic-core's ModelRouter policy.
- When the AI path is unavailable (no key / flag off / error) a deterministic
  heuristic scorer produces a score + rationale + pass/fail, so the demo and the
  test suite never depend on live LLM calls.
- This is a STRUCTURED assessment, not a live proctored coding test. The
  assessment-adapter seam (execo-bridge src/lib/assessment-adapter.ts) is
  preserved so a real proctored provider can plug in later without changing this
  scoring contract.

No public "top X%" claim is produced anywhere — only "Independently vetted".
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from services.marketplace.constants import VETTING_PASS_THRESHOLD

logger = logging.getLogger("execflex.marketplace.vetting")

# Model ids mirror agentic-core's routing policy (routing.py): mechanical work
# → Haiku, reasoning → Sonnet. Kept as literals here so the marketplace does not
# hard-depend on importing the router at call time.
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-5-20250929"


# ── Question sets ────────────────────────────────────────────────────────────
# Each track shares two leadership questions and adds four track-specific
# technical questions. Weights sum to 1.0 within a track.

_LEADERSHIP_QUESTIONS = [
    {
        "id": "lead_1",
        "competency": "Leadership",
        "weight": 0.15,
        "text": "Describe a time you led a team through a significant technical "
                "or organisational change. What was your role, and how did you "
                "measure success?",
    },
    {
        "id": "lead_2",
        "competency": "Stakeholder Management",
        "weight": 0.15,
        "text": "How do you translate a complex AI/data initiative to a "
                "non-technical executive audience and secure their buy-in?",
    },
]

_TECHNICAL_BY_TRACK: dict[str, list[dict[str, Any]]] = {
    "ml_platform": [
        {"id": "tech_1", "competency": "ML Systems", "weight": 0.20,
         "text": "Walk through how you would design a feature store and model "
                 "serving stack for low-latency inference at scale. What are the "
                 "principal failure modes and how do you mitigate them?"},
        {"id": "tech_2", "competency": "MLOps", "weight": 0.20,
         "text": "How do you manage model versioning, rollout, and rollback in "
                 "production, and how do you detect and respond to model drift?"},
        {"id": "tech_3", "competency": "Reliability", "weight": 0.15,
         "text": "Give a concrete example of an ML platform incident you owned "
                 "end to end: detection, mitigation, and the systemic fix."},
        {"id": "tech_4", "competency": "Cost & Scale", "weight": 0.15,
         "text": "Describe how you reduced the cost or improved the scalability "
                 "of a training or serving pipeline, with the numbers."},
    ],
    "data_engineering": [
        {"id": "tech_1", "competency": "Data Architecture", "weight": 0.20,
         "text": "Design a data platform handling both batch and streaming for a "
                 "company scaling 10x. What are the key trade-offs in your storage "
                 "and processing choices?"},
        {"id": "tech_2", "competency": "Data Quality", "weight": 0.20,
         "text": "How do you guarantee data quality and lineage across a large "
                 "estate of pipelines, and how do you handle a silent data "
                 "corruption discovered weeks later?"},
        {"id": "tech_3", "competency": "Governance", "weight": 0.15,
         "text": "How do you approach data governance, PII handling, and access "
                 "control in a regulated (e.g. GDPR / EU AI Act) environment?"},
        {"id": "tech_4", "competency": "Performance", "weight": 0.15,
         "text": "Describe a specific pipeline you re-architected for performance "
                 "or cost, including the before/after metrics."},
    ],
    "ai_product": [
        {"id": "tech_1", "competency": "AI Product Strategy", "weight": 0.20,
         "text": "How do you decide which problems are a good fit for an AI/LLM "
                 "solution versus a deterministic one, and how do you scope an "
                 "MVP that de-risks the hardest assumption first?"},
        {"id": "tech_2", "competency": "Evaluation", "weight": 0.20,
         "text": "How do you define and measure quality for a generative AI "
                 "feature where there is no single correct answer?"},
        {"id": "tech_3", "competency": "Delivery", "weight": 0.15,
         "text": "Describe an AI product you took from prototype to production. "
                 "What broke at scale that you did not anticipate?"},
        {"id": "tech_4", "competency": "Responsible AI", "weight": 0.15,
         "text": "How do you handle safety, bias, and user trust in an AI product, "
                 "with a concrete example of a guardrail you shipped?"},
    ],
    "applied_research": [
        {"id": "tech_1", "competency": "Research Depth", "weight": 0.20,
         "text": "Describe a research problem you took from an open question to a "
                 "deployed result. How did you decide the approach and know when "
                 "to stop iterating?"},
        {"id": "tech_2", "competency": "Experimentation", "weight": 0.20,
         "text": "How do you design experiments and ablations to attribute a "
                 "measured improvement to the right cause?"},
        {"id": "tech_3", "competency": "Translation", "weight": 0.15,
         "text": "Give an example of translating a research advance into "
                 "production value, and what was lost or added in the transfer."},
        {"id": "tech_4", "competency": "Rigour", "weight": 0.15,
         "text": "How do you guard against fooling yourself — overfitting to a "
                 "benchmark, leakage, or a result that will not reproduce?"},
    ],
}


def question_set(track: str) -> list[dict[str, Any]]:
    """Return the full ordered question set for a vetting track.

    Falls back to the ml_platform set for an unknown track so the caller always
    gets a valid, consistently-ordered set (bias control: every leader in a
    track answers identical questions in identical order).
    """
    technical = _TECHNICAL_BY_TRACK.get(track) or _TECHNICAL_BY_TRACK["ml_platform"]
    return technical + _LEADERSHIP_QUESTIONS


# ── Result type ──────────────────────────────────────────────────────────────

@dataclass
class VettingResult:
    score: int                       # 0-100 overall
    passed: bool
    status: str                      # 'verified' | 'rejected'
    rationale: str                   # explainable, human-readable
    per_competency: list[dict]       # [{competency, score, note}]
    model_used: str                  # which scoring path produced this
    ai_generated: bool
    confidence: str = "medium"
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "passed": self.passed,
            "status": self.status,
            "rationale": self.rationale,
            "per_competency": self.per_competency,
            "model_used": self.model_used,
            "ai_generated": self.ai_generated,
            "confidence": self.confidence,
            "flags": self.flags,
            "threshold": VETTING_PASS_THRESHOLD,
        }


# ── Public entry point ───────────────────────────────────────────────────────

def score_vetting(
    *,
    leader_name: str,
    track: str,
    responses: list[dict[str, Any]],
    client: Any | None = None,
) -> VettingResult:
    """Score a leader's vetting responses.

    responses: [{question_id, competency, weight, text}, ...]

    Tries the AI path (Haiku per-answer + Sonnet rationale); on any failure or
    when disabled, returns a deterministic heuristic result. Always returns a
    VettingResult — never raises for scoring reasons.
    """
    responses = responses or []
    if _ai_enabled():
        client = client or _get_client()
        if client is not None:
            try:
                return _score_with_ai(leader_name, track, responses, client)
            except Exception:
                logger.exception("Vetting AI path failed — falling back to heuristic")
    return _score_heuristic(leader_name, track, responses)


# ── AI path ──────────────────────────────────────────────────────────────────

def _score_with_ai(leader_name, track, responses, client) -> VettingResult:
    # 1) Mechanical per-answer scoring → Haiku. One structured call scores every
    #    answer 0-100 with a one-line note. Cheap, deterministic-ish work.
    scored = _haiku_score_answers(client, track, responses)

    # 2) Reasoning → Sonnet. Produce the overall explainable rationale and a
    #    pass/fail recommendation grounded in the per-answer scores.
    weights = {r.get("question_id") or r.get("id"): float(r.get("weight") or 0) for r in responses}
    # Fall back to equal weights if none supplied.
    if not any(weights.values()):
        weights = {k: 1.0 / max(len(scored), 1) for k in [s["question_id"] for s in scored]}
    total_w = sum(weights.get(s["question_id"], 0) for s in scored) or 1.0
    overall = round(sum(s["score"] * weights.get(s["question_id"], 0) for s in scored) / total_w)
    overall = max(0, min(100, overall))

    rationale, confidence, flags = _sonnet_rationale(client, leader_name, track, scored, overall)

    passed = overall >= VETTING_PASS_THRESHOLD
    per_comp = [{"competency": s["competency"], "score": s["score"], "note": s["note"]} for s in scored]
    return VettingResult(
        score=overall,
        passed=passed,
        status="verified" if passed else "rejected",
        rationale=rationale,
        per_competency=per_comp,
        model_used=f"marketplace_vetting_v1 (haiku+sonnet)",
        ai_generated=True,
        confidence=confidence,
        flags=flags,
    )


def _haiku_score_answers(client, track, responses) -> list[dict]:
    items = [
        {"question_id": r.get("question_id") or r.get("id"),
         "competency": r.get("competency", "General"),
         "answer": (r.get("text") or r.get("response") or "").strip()}
        for r in responses
    ]
    prompt = (
        "You are scoring a candidate's answers for a senior AI/data leadership "
        f"vetting assessment (track: {track}). For EACH answer, assign an integer "
        "score 0-100 reflecting depth, specificity, evidence (numbers, concrete "
        "systems, ownership), and seniority. Penalise vagueness and buzzwords; "
        "reward concrete, quantified, first-person examples. Return ONLY a JSON "
        'array: [{"question_id": "...", "score": 0-100, "note": "<=12 words"}].\n\n'
        f"ANSWERS:\n{json.dumps(items, ensure_ascii=False)}"
    )
    resp = client.complete(prompt, model=HAIKU_MODEL, max_tokens=1200, temperature=0.0,
                           system="You are a precise, terse technical assessor. Output JSON only.")
    parsed = _extract_json(resp.text)
    by_id = {}
    if isinstance(parsed, list):
        for row in parsed:
            if isinstance(row, dict) and row.get("question_id"):
                by_id[row["question_id"]] = row
    out = []
    for it in items:
        row = by_id.get(it["question_id"], {})
        raw = row.get("score")
        score = int(max(0, min(100, raw))) if isinstance(raw, (int, float)) else _heuristic_answer_score(it["answer"])
        out.append({
            "question_id": it["question_id"],
            "competency": it["competency"],
            "score": score,
            "note": (row.get("note") or "").strip()[:80] or "scored",
        })
    return out


def _sonnet_rationale(client, leader_name, track, scored, overall) -> tuple[str, str, list[str]]:
    prompt = (
        f"A senior AI/data leader ({leader_name}) completed an independent vetting "
        f"assessment on the '{track}' track. Per-competency scores (0-100):\n"
        f"{json.dumps(scored, ensure_ascii=False)}\n"
        f"Weighted overall: {overall}/100. Pass threshold: {VETTING_PASS_THRESHOLD}.\n\n"
        "Write a concise, explainable rationale (3-4 sentences) a hiring company "
        "would read next to a 'Independently vetted' badge. State the standout "
        "strengths and any reservation, grounded in the scores. Do NOT invent "
        "facts not implied by the scores. Do NOT use any percentile or 'top X%' "
        'language. Then return ONLY JSON: {"rationale": "...", "confidence": '
        '"low|medium|high", "flags": ["..."]}. flags = short risk notes or [].'
    )
    resp = client.complete(prompt, model=SONNET_MODEL, max_tokens=700, temperature=0.4,
                           system="You are an impartial vetting reviewer. Output JSON only.")
    parsed = _extract_json(resp.text)
    if isinstance(parsed, dict) and parsed.get("rationale"):
        conf = parsed.get("confidence") if parsed.get("confidence") in ("low", "medium", "high") else "medium"
        flags = parsed.get("flags") if isinstance(parsed.get("flags"), list) else []
        return parsed["rationale"].strip(), conf, [str(f)[:120] for f in flags][:5]
    # Sonnet returned unparseable text — synthesise from scores.
    return _heuristic_rationale(leader_name, scored, overall), "low", []


# ── Deterministic heuristic path (no LLM) ────────────────────────────────────

def _score_heuristic(leader_name, track, responses) -> VettingResult:
    scored = []
    for r in responses:
        answer = (r.get("text") or r.get("response") or "").strip()
        scored.append({
            "question_id": r.get("question_id") or r.get("id") or f"q{len(scored)}",
            "competency": r.get("competency", "General"),
            "score": _heuristic_answer_score(answer),
            "note": "heuristic score (specificity, evidence, length)",
        })
    weights = [float(r.get("weight") or 0) for r in responses]
    if scored and any(weights):
        tw = sum(weights) or 1.0
        overall = round(sum(s["score"] * w for s, w in zip(scored, weights)) / tw)
    elif scored:
        overall = round(sum(s["score"] for s in scored) / len(scored))
    else:
        overall = 0
    overall = max(0, min(100, overall))
    passed = overall >= VETTING_PASS_THRESHOLD
    return VettingResult(
        score=overall,
        passed=passed,
        status="verified" if passed else "rejected",
        rationale=_heuristic_rationale(leader_name, scored, overall),
        per_competency=[{"competency": s["competency"], "score": s["score"], "note": s["note"]} for s in scored],
        model_used="marketplace_vetting_v1 (heuristic)",
        ai_generated=False,
        confidence="low",
        flags=[] if passed else ["Below vetting threshold on heuristic scoring."],
    )


# Signals that a senior technical answer is substantive: quantified evidence,
# concrete systems/ownership language, and sufficient depth.
_EVIDENCE_RE = re.compile(r"\d")
_CONCRETE_TERMS = (
    "latency", "throughput", "pipeline", "model", "production", "incident",
    "rollback", "drift", "governance", "lineage", "experiment", "ablation",
    "team", "stakeholder", "cost", "scale", "sla", "metric", "%", "led", "owned",
    "designed", "reduced", "improved", "shipped", "deployed",
)


def _heuristic_answer_score(answer: str) -> int:
    if not answer:
        return 0
    words = answer.split()
    n = len(words)
    # Length component: rewards a substantive answer, saturates ~120 words.
    length = min(1.0, n / 120.0)
    # Evidence: presence of numbers (quantified claims).
    evidence = 1.0 if _EVIDENCE_RE.search(answer) else 0.0
    # Concreteness: distinct domain/ownership terms used.
    lower = answer.lower()
    hits = sum(1 for t in _CONCRETE_TERMS if t in lower)
    concrete = min(1.0, hits / 6.0)
    # First-person ownership.
    ownership = 1.0 if re.search(r"\b(i|we|my|our)\b", lower) else 0.4
    raw = 0.30 * length + 0.30 * evidence + 0.30 * concrete + 0.10 * ownership
    # Very short answers are capped hard.
    if n < 15:
        raw = min(raw, 0.45)
    return int(round(max(0, min(100, raw * 100))))


def _heuristic_rationale(leader_name, scored, overall) -> str:
    if not scored:
        return (f"{leader_name} submitted no assessable responses; unable to verify. "
                f"Overall {overall}/100, below the {VETTING_PASS_THRESHOLD} threshold.")
    ranked = sorted(scored, key=lambda s: s["score"], reverse=True)
    top = ranked[0]
    low = ranked[-1]
    verdict = "meets" if overall >= VETTING_PASS_THRESHOLD else "does not yet meet"
    return (
        f"{leader_name} scored {overall}/100 overall and {verdict} the independent "
        f"vetting bar ({VETTING_PASS_THRESHOLD}). Strongest evidence in "
        f"{top['competency']} ({top['score']}/100); weakest in {low['competency']} "
        f"({low['score']}/100). Scored on specificity, quantified evidence, and "
        f"ownership across a fixed technical + leadership question set."
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ai_enabled() -> bool:
    if os.environ.get("MARKETPLACE_VETTING_AI", "").lower() in ("0", "false", "off", "no"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from agentic_core.primitives.llm.anthropic_client import AnthropicClient
        return AnthropicClient(api_key=api_key)
    except Exception:
        logger.warning("agentic-core AnthropicClient unavailable — vetting uses heuristic path")
        return None


def _extract_json(text: str):
    """Best-effort extract a JSON object/array from model text."""
    if not text:
        return None
    text = text.strip()
    # Strip code fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # Find first {...} or [...] block.
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = text.find(opener), text.rfind(closer)
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    return None
