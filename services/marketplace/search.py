"""Marketplace search engine — ranked, explainable search over the leader pool.

This is the "search marketplace": a free-text query is scored for relevance
across a leader's headline, skills, sectors, bio, seniority and discipline, and
combined with structured facets (skill, discipline/track, seniority, engagement,
sector, comp range). Results are RANKED by a relevance score (not merely
filtered) and every result carries the reasons it matched.

An optional agentic-core LLM re-rank (behind MARKETPLACE_SEARCH_AI) performs a
semantic pass over the top lexical candidates for queries like
"someone who's scaled a data platform in fintech". It degrades gracefully to the
lexical ranking on any failure, so the product and tests never depend on tokens.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from services.marketplace import store
from services.marketplace.constants import TRACK_LABELS

logger = logging.getLogger("execflex.marketplace.search")

SONNET_MODEL = "claude-sonnet-4-5-20250929"

# Very common words that carry no ranking signal.
_STOP = {
    "a", "an", "the", "and", "or", "of", "to", "in", "for", "with", "who", "someone",
    "me", "find", "looking", "need", "want", "has", "have", "that", "scaled", "scale",
    "is", "at", "on", "as", "by", "from", "person", "leader", "expert", "experience",
    "experienced", "strong", "good", "great", "our", "their", "them", "i", "we",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9+#]+", (text or "").lower()) if t and t not in _STOP]


def _parse_comp(comp: str) -> tuple[Optional[float], Optional[float]]:
    """Best-effort extraction of an annual EUR range from a comp string.

    Handles "€180k–220k", "€150k-175k", "€1,300/day", "140000". Day rates are
    annualised at ~220 working days so comp-range filters behave sensibly.
    """
    if not comp:
        return None, None
    s = comp.lower().replace(",", "").replace("–", "-").replace("—", "-")
    is_day = "day" in s or "/d" in s
    nums = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(k)?", s):
        val = float(m.group(1))
        if m.group(2) == "k":
            val *= 1000
        if val >= 100:  # ignore stray small numbers
            nums.append(val)
    if not nums:
        return None, None
    lo, hi = min(nums), max(nums)
    if is_day:
        lo, hi = lo * 220, hi * 220
    return lo, hi


@dataclass
class SearchResult:
    leader: dict
    relevance: float                 # 0-100
    match_reasons: list[str] = field(default_factory=list)
    semantic: bool = False


def _score_leader(leader: dict, tokens: list[str]) -> tuple[float, list[str]]:
    """Lexical relevance of one leader to the query tokens, with reasons."""
    if not tokens:
        # No free-text query — quality prior only (handled by caller).
        return 0.0, []

    reasons: list[str] = []
    score = 0.0
    qset = set(tokens)

    skills = [s for s in (leader.get("skills") or [])]
    headline = leader.get("headline") or ""
    headline_tokens = set(_tokenize(headline))
    sectors = [s for s in (leader.get("sectors") or [])]
    bio_tokens = set(_tokenize(leader.get("bio") or ""))
    seniority_tokens = set(_tokenize(leader.get("seniority") or ""))
    discipline = leader.get("discipline") or ""
    discipline_tokens = set(_tokenize(discipline))
    name_tokens = set(_tokenize(leader.get("name") or ""))

    # Skills are the strongest signal.
    matched_skills = [s for s in skills if qset & set(_tokenize(s))]
    if matched_skills:
        # 24 for the first matched skill, +12 for each additional (cap ~2 extra).
        score += 24 + 12 * min(2, len(matched_skills) - 1)
        reasons.append("Skills: " + ", ".join(matched_skills[:4]))

    # Headline phrase / token hits.
    hl_hits = qset & headline_tokens
    if hl_hits:
        score += 18 * min(1.0, len(hl_hits) / 2.0) + 6
        reasons.append(f"Headline matches “{headline[:70]}”")

    # Sector / domain.
    matched_sectors = [s for s in sectors if qset & set(_tokenize(s))]
    if matched_sectors:
        score += 16
        reasons.append("Sector: " + ", ".join(matched_sectors[:3]))

    # Discipline / track.
    if qset & discipline_tokens:
        score += 12
        reasons.append(f"Discipline: {discipline}")

    # Seniority.
    if qset & seniority_tokens:
        score += 8
        reasons.append(f"Seniority: {leader.get('seniority')}")

    # Bio / name (weaker).
    bio_hits = qset & bio_tokens
    if bio_hits:
        score += 6
        reasons.append("Mentioned in profile summary")
    if qset & name_tokens:
        score += 20
        reasons.append("Name match")

    return score, reasons


def search_leaders(
    *,
    query: str = "",
    skill: Optional[str] = None,
    track: Optional[str] = None,
    seniority: Optional[str] = None,
    engagement: Optional[str] = None,
    sector: Optional[str] = None,
    comp_min: Optional[float] = None,
    comp_max: Optional[float] = None,
    limit: int = 50,
    use_ai: Optional[bool] = None,
) -> dict:
    """Ranked, explainable search over the verified leader pool.

    Structured facets (track/engagement/sector/seniority/skill/comp) are applied
    as filters; the free-text query ranks the survivors by relevance. Returns
    {"results": [...], "total": n, "query": ..., "semantic": bool}.
    """
    query = (query or "").strip()
    tokens = _tokenize(query)

    # Hard structured facets first (delegates skill/sector/seniority/track/engagement).
    pool = store.list_leaders(
        status="verified", skill=skill, seniority=seniority,
        engagement=engagement, sector=sector, track=track, limit=1000,
    )

    # Comp-range facet (best-effort parse).
    if comp_min is not None or comp_max is not None:
        def comp_ok(ld: dict) -> bool:
            lo, hi = _parse_comp(ld.get("comp_expectation") or "")
            if lo is None and hi is None:
                return True  # unknown comp → don't exclude
            if comp_min is not None and hi is not None and hi < comp_min:
                return False
            if comp_max is not None and lo is not None and lo > comp_max:
                return False
            return True
        pool = [ld for ld in pool if comp_ok(ld)]

    scored: list[SearchResult] = []
    for ld in pool:
        rel, reasons = _score_leader(ld, tokens)
        # Vetting score is a mild quality prior and the primary sort key when
        # there is no free-text query.
        vscore = ld.get("vetting_score") or 0
        if tokens:
            combined = rel + 0.15 * vscore
        else:
            combined = float(vscore)
            reasons = [f"Independently vetted · {vscore}/100"] if vscore else []
        # Skip zero-relevance leaders when a query is present (keep it a search,
        # not a filtered dump) — unless the query matched nothing at all.
        scored.append(SearchResult(leader=ld, relevance=round(combined, 1), match_reasons=reasons))

    if tokens:
        # Keep only leaders with any lexical signal; if none matched, fall back
        # to the full pool ranked by quality so the user still gets results.
        with_signal = [s for s in scored if s.match_reasons]
        scored = with_signal if with_signal else scored

    scored.sort(key=lambda s: s.relevance, reverse=True)
    top = scored[:limit]

    semantic = False
    if _ai_enabled(use_ai) and tokens and len(top) > 1:
        try:
            top = _ai_rerank(query, top)
            semantic = True
        except Exception:
            logger.exception("Search AI re-rank failed — using lexical ranking")

    # Normalise relevance to a friendly 0-100 for display.
    results = []
    for i, s in enumerate(top):
        results.append({
            **s.leader,
            "relevance": s.relevance,
            "match_reasons": s.match_reasons,
            "semantic": s.semantic,
            "rank": i + 1,
        })
    return {"results": results, "total": len(results), "query": query, "semantic": semantic}


# ── Optional agentic-core semantic re-rank ───────────────────────────────────

def _ai_enabled(explicit: Optional[bool]) -> bool:
    if explicit is not None:
        return bool(explicit) and bool(os.environ.get("ANTHROPIC_API_KEY"))
    flag = os.environ.get("MARKETPLACE_SEARCH_AI", "").lower()
    if flag in ("1", "true", "on", "yes"):
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return False


def _get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from agentic_core.primitives.llm.anthropic_client import AnthropicClient
        return AnthropicClient(api_key=api_key)
    except Exception:
        return None


def _ai_rerank(query: str, top: list[SearchResult]) -> list[SearchResult]:
    """Ask Sonnet to reorder the top candidates by semantic fit and add a reason."""
    client = _get_client()
    if client is None:
        return top
    candidates = [{
        "id": s.leader.get("id"),
        "headline": s.leader.get("headline"),
        "discipline": s.leader.get("discipline"),
        "skills": s.leader.get("skills"),
        "sectors": s.leader.get("sectors"),
        "seniority": s.leader.get("seniority"),
    } for s in top[:15]]
    prompt = (
        f'A hiring company searched the vetted AI/data leader pool for: "{query}".\n'
        "Re-rank the candidates below by how well each SEMANTICALLY fits that "
        "intent (not just keyword overlap). Return ONLY a JSON array, best first: "
        '[{"id": "...", "reason": "<=14 words why this leader fits the search"}]. '
        "Include only genuinely relevant candidates.\n\n"
        f"CANDIDATES:\n{json.dumps(candidates, ensure_ascii=False)}"
    )
    resp = client.complete(prompt, model=SONNET_MODEL, max_tokens=900, temperature=0.2,
                           system="You are a precise technical recruiter. Output JSON only.")
    order = _extract_json(resp.text)
    if not isinstance(order, list):
        return top
    by_id = {s.leader.get("id"): s for s in top}
    reranked: list[SearchResult] = []
    for row in order:
        if not isinstance(row, dict):
            continue
        s = by_id.pop(row.get("id"), None)
        if s is None:
            continue
        reason = (row.get("reason") or "").strip()
        reasons = ([f"AI match: {reason}"] if reason else []) + s.match_reasons
        reranked.append(SearchResult(leader=s.leader, relevance=s.relevance,
                                     match_reasons=reasons[:4], semantic=True))
    # Append any the model dropped, preserving lexical order.
    reranked.extend(by_id.values())
    return reranked


def _extract_json(text: str):
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).rstrip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    i, j = text.find("["), text.rfind("]")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            return None
    return None
