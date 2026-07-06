"""Feature flags for AI agent enablement.

All flags default to False (agents disabled). Set environment variables
to enable:

    EXECFLEX_AI_MATCH_RERANK=1       — LLM re-ranking on match results
    EXECFLEX_AI_SCREENING_SUMMARY=1  — LLM screening transcript summary
    EXECFLEX_AI_CV_PARSER=1          — LLM CV/resume parsing
    EXECFLEX_AI_JD_GENERATOR=1       — LLM job description generation
    EXECFLEX_AI_QUESTION_FLOW=1      — Per-role configurable question flows
    EXECFLEX_AI_COMPLIANCE_CHECK=1   — EU AI Act compliance (snapshot + prohibited)

Decision D-26: Feature flags are environment-variable based, not per-org.
Per-org flags require a settings table and admin UI — cut for v1.
"""

import os


def _is_enabled(var_name: str) -> bool:
    return os.environ.get(var_name, "").strip().lower() in ("1", "true", "yes")


def match_rerank_enabled() -> bool:
    return _is_enabled("EXECFLEX_AI_MATCH_RERANK")


def screening_summary_enabled() -> bool:
    return _is_enabled("EXECFLEX_AI_SCREENING_SUMMARY")


def cv_parser_enabled() -> bool:
    return _is_enabled("EXECFLEX_AI_CV_PARSER")


def jd_generator_enabled() -> bool:
    return _is_enabled("EXECFLEX_AI_JD_GENERATOR")


def question_flow_enabled() -> bool:
    return _is_enabled("EXECFLEX_AI_QUESTION_FLOW")


def compliance_check_enabled() -> bool:
    return _is_enabled("EXECFLEX_AI_COMPLIANCE_CHECK")


def any_ai_enabled() -> bool:
    return any([
        match_rerank_enabled(),
        screening_summary_enabled(),
        cv_parser_enabled(),
        jd_generator_enabled(),
        question_flow_enabled(),
        compliance_check_enabled(),
    ])


def get_flags_status() -> dict[str, bool]:
    return {
        "match_rerank": match_rerank_enabled(),
        "screening_summary": screening_summary_enabled(),
        "cv_parser": cv_parser_enabled(),
        "jd_generator": jd_generator_enabled(),
        "question_flow": question_flow_enabled(),
        "compliance_check": compliance_check_enabled(),
    }
