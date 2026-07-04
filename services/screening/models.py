from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ScreeningSessionType(Enum):
    CANDIDATE = "candidate"
    CLIENT = "client"


@dataclass
class Question:
    id: str
    text: str
    category: str
    weight: float = 1.0
    required: bool = True


@dataclass
class Answer:
    question_id: str
    response_text: str
    score: Optional[float] = None
    score_justification: str = ""
    extracted_facts: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScreeningOutcome:
    recommendation: str
    overall_score: float
    answers: List[Answer] = field(default_factory=list)
    extracted_facts: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    @property
    def is_positive(self) -> bool:
        return self.recommendation in ("strong_proceed", "proceed")


@dataclass
class StructuredBrief:
    role_title: str = ""
    role_description: str = ""
    requirements_must_have: List[str] = field(default_factory=list)
    requirements_nice_to_have: List[str] = field(default_factory=list)
    deal_breakers: List[str] = field(default_factory=list)
    team_size: str = ""
    timeline: str = ""
    budget_range: str = ""
    culture_notes: str = ""
    raw_answers: List[Answer] = field(default_factory=list)


CANDIDATE_QUESTIONS = [
    Question(id="c_background", text="Can you tell me about your professional background and current role?", category="background", weight=1.0),
    Question(id="c_experience", text="How many years of experience do you have in this field?", category="experience", weight=1.0),
    Question(id="c_skills", text="What are your core skills and areas of expertise?", category="skills", weight=1.5),
    Question(id="c_motivation", text="What's motivating your search for a new opportunity?", category="motivation", weight=0.8),
    Question(id="c_availability", text="What's your availability and preferred working arrangement?", category="availability", weight=1.0),
    Question(id="c_compensation", text="What are your compensation expectations?", category="compensation", weight=0.8),
    Question(id="c_location", text="Where are you based, and are you open to relocation or remote work?", category="location", weight=0.8),
]

CLIENT_QUESTIONS = [
    Question(id="cl_role", text="What role are you looking to fill?", category="role_description", weight=1.5),
    Question(id="cl_requirements", text="What are the must-have requirements for this role?", category="requirements", weight=1.5),
    Question(id="cl_nice_to_have", text="What are the nice-to-have qualifications?", category="nice_to_have", weight=0.8),
    Question(id="cl_team", text="Can you describe the team this person would join?", category="team", weight=0.8),
    Question(id="cl_timeline", text="What's your timeline for filling this position?", category="timeline", weight=1.0),
    Question(id="cl_budget", text="What's the budget range for this role?", category="budget", weight=1.0),
    Question(id="cl_culture", text="How would you describe your company culture?", category="culture", weight=0.5),
]


@dataclass
class ScreeningSession:
    session_id: str
    session_type: ScreeningSessionType
    role_id: Optional[str] = None
    candidate_id: Optional[str] = None
    client_id: Optional[str] = None
    consent_given: bool = False
    questions: List[Question] = field(default_factory=list)
    answers: List[Answer] = field(default_factory=list)
    current_question_index: int = 0
    outcome: Optional[ScreeningOutcome] = None
    brief: Optional[StructuredBrief] = None
    handoff_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.questions:
            if self.session_type == ScreeningSessionType.CANDIDATE:
                self.questions = list(CANDIDATE_QUESTIONS)
            else:
                self.questions = list(CLIENT_QUESTIONS)

    @property
    def current_question(self) -> Optional[Question]:
        if self.current_question_index < len(self.questions):
            return self.questions[self.current_question_index]
        return None

    @property
    def is_complete(self) -> bool:
        return self.current_question_index >= len(self.questions)

    @property
    def progress(self) -> float:
        if not self.questions:
            return 0.0
        return self.current_question_index / len(self.questions)
