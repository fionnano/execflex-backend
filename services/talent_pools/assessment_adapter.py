"""
Pluggable assessment adapter interface for ExecFlex Verified pools.

Adapters for Codility-class tools (tech), finance skill assessments, etc.
This is design + scaffold only — no real assessment engine is built.
See VERIFICATION_METHODOLOGY.md for how "verified" claims are measured.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class AssessmentResult:
    provider: str
    candidate_id: str
    pool_id: str
    passed: bool
    score: float  # 0-100
    max_score: float = 100.0
    details: Dict[str, Any] = field(default_factory=dict)
    assessment_url: Optional[str] = None


class AssessmentAdapter(Protocol):
    """Interface for external assessment providers."""
    provider_name: str

    def create_assessment(self, candidate_id: str, pool_id: str,
                          criteria: Dict[str, Any]) -> str:
        """Create an assessment invitation. Returns an assessment URL or ID."""
        ...

    def get_result(self, assessment_id: str) -> Optional[AssessmentResult]:
        """Fetch the result of a completed assessment."""
        ...

    def list_available_tests(self) -> List[Dict[str, str]]:
        """List available test types from this provider."""
        ...


class StubAssessmentAdapter:
    """Stub adapter for local dev/testing — always returns a passing score."""
    provider_name = "stub"

    def create_assessment(self, candidate_id: str, pool_id: str,
                          criteria: Dict[str, Any]) -> str:
        return f"stub-assessment-{candidate_id[:8]}"

    def get_result(self, assessment_id: str) -> Optional[AssessmentResult]:
        return AssessmentResult(
            provider=self.provider_name,
            candidate_id=assessment_id.replace("stub-assessment-", ""),
            pool_id="",
            passed=True,
            score=85.0,
            details={"note": "Stub assessment — always passes"},
        )

    def list_available_tests(self) -> List[Dict[str, str]]:
        return [
            {"id": "tech-general", "name": "General Technical Assessment", "category": "tech"},
            {"id": "finance-general", "name": "Financial Analysis Assessment", "category": "finance"},
            {"id": "leadership", "name": "Leadership Assessment", "category": "management"},
        ]
