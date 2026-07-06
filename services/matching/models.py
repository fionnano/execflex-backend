from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class Candidate:
    id: str
    name: str
    headline: str = ""
    industries: Set[str] = field(default_factory=set)
    skills: Set[str] = field(default_factory=set)
    experience_years: int = 0
    location: str = ""
    availability: str = ""
    compensation_min: int = 0
    compensation_max: int = 0
    is_ned_available: bool = False
    screening_recommendation: str = ""
    screening_score: float = 0.0
    open_to_opportunities: str = ""
    preferred_role_type: str = ""

    def __post_init__(self):
        if isinstance(self.industries, list):
            self.industries = {s.strip().lower() for s in self.industries if s}
        elif isinstance(self.industries, set):
            self.industries = {s.strip().lower() for s in self.industries if s}
        if isinstance(self.skills, list):
            self.skills = {s.strip().lower() for s in self.skills if s}
        elif isinstance(self.skills, set):
            self.skills = {s.strip().lower() for s in self.skills if s}
        self.location = (self.location or "").strip().lower()
        self.availability = (self.availability or "").strip().lower()
        self.screening_recommendation = (self.screening_recommendation or "").strip().lower()
        self.open_to_opportunities = (self.open_to_opportunities or "").strip().lower()
        self.preferred_role_type = (self.preferred_role_type or "").strip().lower()


@dataclass
class Role:
    id: str
    title: str
    industry: str = ""
    required_skills: Set[str] = field(default_factory=set)
    min_experience: int = 0
    location: str = ""
    commitment_type: str = ""
    budget_min: int = 0
    budget_max: int = 0
    is_ned: bool = False
    description: str = ""

    def __post_init__(self):
        self.industry = (self.industry or "").strip().lower()
        if isinstance(self.required_skills, list):
            self.required_skills = {s.strip().lower() for s in self.required_skills if s}
        elif isinstance(self.required_skills, set):
            self.required_skills = {s.strip().lower() for s in self.required_skills if s}
        self.location = (self.location or "").strip().lower()
        self.commitment_type = (self.commitment_type or "").strip().lower()


@dataclass
class DimensionScore:
    score: float
    reason: str


@dataclass
class MatchExplanation:
    dimension_scores: Dict[str, DimensionScore]
    composite_score: float
    summary: str


@dataclass
class MatchResult:
    candidate: Candidate
    explanation: MatchExplanation
    rank: int = 0

    @property
    def score(self) -> float:
        return self.explanation.composite_score
