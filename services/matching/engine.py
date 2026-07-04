import re
from typing import Dict, List, Optional, Protocol

from .models import Candidate, DimensionScore, MatchExplanation, MatchResult, Role


DEFAULT_WEIGHTS = {
    "industry_fit": 0.20,
    "skills_fit": 0.25,
    "experience_fit": 0.15,
    "location_fit": 0.10,
    "availability_fit": 0.10,
    "compensation_fit": 0.10,
    "screening_fit": 0.10,
}


class Reranker(Protocol):
    def rerank(
        self, matches: List[MatchResult], role: Role, context: str
    ) -> List[MatchResult]: ...


def _tokenize(text: str) -> set:
    return {t.strip().lower() for t in re.split(r"[,/;|\s]+", text) if t.strip()}


def _set_overlap_score(candidate_set: set, role_set: set) -> float:
    if not role_set:
        return 100.0
    if not candidate_set:
        return 0.0
    overlap = candidate_set & role_set
    return min(100.0, (len(overlap) / len(role_set)) * 100.0)


def _score_industry(candidate: Candidate, role: Role) -> DimensionScore:
    if not role.industry:
        return DimensionScore(score=50.0, reason="No industry requirement specified")

    role_industries = _tokenize(role.industry)
    overlap = candidate.industries & role_industries

    if not candidate.industries:
        return DimensionScore(score=0.0, reason="Candidate has no industry data")

    score = _set_overlap_score(candidate.industries, role_industries)
    if score >= 80:
        reason = f"Strong industry match: {', '.join(sorted(overlap))}"
    elif score > 0:
        reason = f"Partial industry overlap: {', '.join(sorted(overlap))}"
    else:
        reason = f"No industry overlap (candidate: {', '.join(sorted(candidate.industries))})"
    return DimensionScore(score=score, reason=reason)


def _score_skills(candidate: Candidate, role: Role) -> DimensionScore:
    if not role.required_skills:
        return DimensionScore(score=50.0, reason="No skill requirements specified")

    if not candidate.skills:
        headline_tokens = _tokenize(candidate.headline)
        overlap = headline_tokens & role.required_skills
        if overlap:
            score = min(100.0, (len(overlap) / len(role.required_skills)) * 80.0)
            return DimensionScore(
                score=score,
                reason=f"Skills inferred from headline: {', '.join(sorted(overlap))}",
            )
        return DimensionScore(score=0.0, reason="Candidate has no skills data")

    overlap = candidate.skills & role.required_skills
    score = _set_overlap_score(candidate.skills, role.required_skills)

    if score >= 80:
        reason = f"Strong skills match: {', '.join(sorted(overlap))}"
    elif score > 0:
        missing = role.required_skills - candidate.skills
        reason = f"Partial skills match ({', '.join(sorted(overlap))}); missing: {', '.join(sorted(missing))}"
    else:
        reason = f"No skills overlap with requirements"
    return DimensionScore(score=score, reason=reason)


def _score_experience(candidate: Candidate, role: Role) -> DimensionScore:
    if role.min_experience <= 0:
        return DimensionScore(score=50.0, reason="No experience requirement specified")

    if candidate.experience_years <= 0:
        return DimensionScore(score=0.0, reason="Candidate has no experience data")

    ratio = candidate.experience_years / role.min_experience
    if ratio >= 1.5:
        score = 100.0
        reason = f"{candidate.experience_years} years exceeds {role.min_experience}-year requirement significantly"
    elif ratio >= 1.0:
        score = 90.0
        reason = f"{candidate.experience_years} years meets {role.min_experience}-year requirement"
    elif ratio >= 0.7:
        score = 60.0
        reason = f"{candidate.experience_years} years is slightly below {role.min_experience}-year requirement"
    else:
        score = max(0.0, ratio * 50.0)
        reason = f"{candidate.experience_years} years is well below {role.min_experience}-year requirement"
    return DimensionScore(score=score, reason=reason)


def _score_location(candidate: Candidate, role: Role) -> DimensionScore:
    if not role.location:
        return DimensionScore(score=50.0, reason="No location requirement specified")

    if not candidate.location:
        return DimensionScore(score=0.0, reason="Candidate has no location data")

    role_loc = role.location.lower()
    cand_loc = candidate.location.lower()

    if "remote" in role_loc or "remote" in cand_loc:
        return DimensionScore(score=90.0, reason="Remote work available")

    if role_loc == cand_loc:
        return DimensionScore(score=100.0, reason=f"Exact location match: {candidate.location}")

    if role_loc in cand_loc or cand_loc in role_loc:
        return DimensionScore(score=80.0, reason=f"Location overlap: {candidate.location} / {role.location}")

    role_tokens = _tokenize(role_loc)
    cand_tokens = _tokenize(cand_loc)
    if role_tokens & cand_tokens:
        shared = role_tokens & cand_tokens
        return DimensionScore(
            score=70.0,
            reason=f"Partial location match: {', '.join(sorted(shared))}",
        )

    return DimensionScore(score=20.0, reason=f"Location mismatch: {candidate.location} vs {role.location}")


def _score_availability(candidate: Candidate, role: Role) -> DimensionScore:
    if not role.commitment_type:
        return DimensionScore(score=50.0, reason="No commitment type specified")

    if not candidate.availability:
        return DimensionScore(score=30.0, reason="Candidate has no availability data")

    role_type = role.commitment_type.lower()
    cand_avail = candidate.availability.lower()
    cand_pref = candidate.preferred_role_type.lower() if candidate.preferred_role_type else ""

    if role_type in cand_avail or cand_avail in role_type:
        return DimensionScore(score=100.0, reason=f"Availability matches: {candidate.availability}")

    if cand_pref and (role_type in cand_pref or cand_pref in role_type):
        return DimensionScore(
            score=90.0,
            reason=f"Preferred role type matches: {candidate.preferred_role_type}",
        )

    return DimensionScore(
        score=30.0,
        reason=f"Availability mismatch: {candidate.availability} vs {role.commitment_type}",
    )


def _score_compensation(candidate: Candidate, role: Role) -> DimensionScore:
    if role.budget_max <= 0:
        return DimensionScore(score=50.0, reason="No budget specified")

    if candidate.compensation_min <= 0 and candidate.compensation_max <= 0:
        return DimensionScore(score=50.0, reason="Candidate has no compensation data")

    cand_ask = candidate.compensation_min or candidate.compensation_max

    if cand_ask <= role.budget_max:
        if role.budget_min > 0 and cand_ask >= role.budget_min:
            return DimensionScore(score=100.0, reason=f"Compensation within budget range")
        return DimensionScore(score=90.0, reason=f"Compensation within budget")

    overshoot = (cand_ask - role.budget_max) / role.budget_max
    if overshoot <= 0.1:
        return DimensionScore(score=70.0, reason=f"Compensation slightly above budget (within 10%)")
    elif overshoot <= 0.25:
        return DimensionScore(score=40.0, reason=f"Compensation above budget by {int(overshoot*100)}%")
    else:
        return DimensionScore(score=10.0, reason=f"Compensation significantly above budget")


def _score_screening(candidate: Candidate, role: Role) -> DimensionScore:
    rec = candidate.screening_recommendation
    if not rec:
        return DimensionScore(score=50.0, reason="Not yet screened")

    scores = {
        "strong_proceed": 100.0,
        "proceed": 80.0,
        "hold": 40.0,
        "reject": 10.0,
    }
    score = scores.get(rec, 50.0)

    if candidate.screening_score > 0:
        reason = f"Screening: {rec} (score: {candidate.screening_score:.1f}/5)"
    else:
        reason = f"Screening recommendation: {rec}"
    return DimensionScore(score=score, reason=reason)


_DIMENSION_SCORERS = {
    "industry_fit": _score_industry,
    "skills_fit": _score_skills,
    "experience_fit": _score_experience,
    "location_fit": _score_location,
    "availability_fit": _score_availability,
    "compensation_fit": _score_compensation,
    "screening_fit": _score_screening,
}


def _generate_summary(dimensions: Dict[str, DimensionScore], composite: float) -> str:
    sorted_dims = sorted(dimensions.items(), key=lambda x: x[1].score, reverse=True)

    strengths = [(k, v) for k, v in sorted_dims if v.score >= 70]
    weaknesses = [(k, v) for k, v in sorted_dims if v.score < 40]

    parts = []
    if composite >= 75:
        parts.append("Strong match")
    elif composite >= 50:
        parts.append("Moderate match")
    else:
        parts.append("Weak match")

    if strengths:
        top = strengths[:2]
        strength_reasons = [v.reason.split(":")[0] if ":" in v.reason else v.reason for _, v in top]
        parts.append(f"strengths in {' and '.join(strength_reasons).lower()}")

    if weaknesses:
        weak = weaknesses[:1]
        weak_reasons = [v.reason for _, v in weak]
        parts.append(f"gap: {weak_reasons[0].lower()}")

    return "; ".join(parts) + f" (score: {composite:.0f}/100)"


class MatchEngine:
    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        reranker: Optional[Reranker] = None,
    ):
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self.reranker = reranker

        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            factor = 1.0 / total
            self.weights = {k: v * factor for k, v in self.weights.items()}

    def score_candidate(self, candidate: Candidate, role: Role) -> MatchResult:
        dimensions: Dict[str, DimensionScore] = {}
        for dim_name, scorer in _DIMENSION_SCORERS.items():
            dimensions[dim_name] = scorer(candidate, role)

        composite = sum(
            dimensions[dim].score * self.weights.get(dim, 0.0)
            for dim in dimensions
        )

        if role.is_ned and not candidate.is_ned_available:
            composite *= 0.3
            dimensions["ned_filter"] = DimensionScore(
                score=0.0, reason="Role requires NED availability; candidate not available"
            )

        if candidate.open_to_opportunities == "no":
            composite *= 0.1
            dimensions["openness"] = DimensionScore(
                score=0.0, reason="Candidate not open to opportunities"
            )
        elif candidate.open_to_opportunities == "passive":
            composite *= 0.85
            dimensions["openness"] = DimensionScore(
                score=50.0, reason="Candidate is passive — may need extra outreach"
            )

        composite = max(0.0, min(100.0, composite))
        summary = _generate_summary(dimensions, composite)

        explanation = MatchExplanation(
            dimension_scores=dimensions,
            composite_score=round(composite, 1),
            summary=summary,
        )
        return MatchResult(candidate=candidate, explanation=explanation)

    def match(
        self,
        candidates: List[Candidate],
        role: Role,
        limit: int = 20,
        min_score: float = 0.0,
        context: str = "",
    ) -> List[MatchResult]:
        results = [self.score_candidate(c, role) for c in candidates]

        results = [r for r in results if r.score >= min_score]
        results.sort(key=lambda r: r.score, reverse=True)

        if self.reranker and context:
            results = self.reranker.rerank(results, role, context)

        for i, result in enumerate(results):
            result.rank = i + 1

        return results[:limit]
