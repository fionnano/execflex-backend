"""
AI feature flags and agent service — synthetic test suite.
Tests flag toggling, agent service fallback, and endpoint wiring.
Zero real LLM calls. Zero real data.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock

from services.ai.feature_flags import (
    match_rerank_enabled,
    screening_summary_enabled,
    cv_parser_enabled,
    jd_generator_enabled,
    question_flow_enabled,
    any_ai_enabled,
    get_flags_status,
)


# ─── feature flag basics ────────────────────────────────────────────────────


class TestFeatureFlags:

    def test_all_flags_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert match_rerank_enabled() is False
            assert screening_summary_enabled() is False
            assert cv_parser_enabled() is False
            assert jd_generator_enabled() is False
            assert question_flow_enabled() is False

    def test_flag_enabled_with_1(self):
        with patch.dict(os.environ, {"EXECFLEX_AI_MATCH_RERANK": "1"}):
            assert match_rerank_enabled() is True

    def test_flag_enabled_with_true(self):
        with patch.dict(os.environ, {"EXECFLEX_AI_MATCH_RERANK": "true"}):
            assert match_rerank_enabled() is True

    def test_flag_enabled_with_yes(self):
        with patch.dict(os.environ, {"EXECFLEX_AI_MATCH_RERANK": "yes"}):
            assert match_rerank_enabled() is True

    def test_flag_enabled_case_insensitive(self):
        with patch.dict(os.environ, {"EXECFLEX_AI_MATCH_RERANK": "TRUE"}):
            assert match_rerank_enabled() is True

    def test_flag_disabled_with_0(self):
        with patch.dict(os.environ, {"EXECFLEX_AI_MATCH_RERANK": "0"}):
            assert match_rerank_enabled() is False

    def test_flag_disabled_with_empty(self):
        with patch.dict(os.environ, {"EXECFLEX_AI_MATCH_RERANK": ""}):
            assert match_rerank_enabled() is False

    def test_flag_disabled_with_random(self):
        with patch.dict(os.environ, {"EXECFLEX_AI_MATCH_RERANK": "maybe"}):
            assert match_rerank_enabled() is False

    def test_any_ai_enabled_false_when_all_off(self):
        with patch.dict(os.environ, {}, clear=True):
            assert any_ai_enabled() is False

    def test_any_ai_enabled_true_when_one_on(self):
        with patch.dict(os.environ, {"EXECFLEX_AI_CV_PARSER": "1"}):
            assert any_ai_enabled() is True

    def test_get_flags_status_returns_all_keys(self):
        status = get_flags_status()
        expected_keys = {"match_rerank", "screening_summary", "cv_parser",
                         "jd_generator", "question_flow"}
        assert set(status.keys()) == expected_keys

    def test_get_flags_status_reflects_env(self):
        with patch.dict(os.environ, {
            "EXECFLEX_AI_MATCH_RERANK": "1",
            "EXECFLEX_AI_CV_PARSER": "1",
        }):
            status = get_flags_status()
            assert status["match_rerank"] is True
            assert status["cv_parser"] is True
            assert status["screening_summary"] is False

    def test_each_flag_maps_to_correct_env_var(self):
        env_map = {
            "EXECFLEX_AI_MATCH_RERANK": match_rerank_enabled,
            "EXECFLEX_AI_SCREENING_SUMMARY": screening_summary_enabled,
            "EXECFLEX_AI_CV_PARSER": cv_parser_enabled,
            "EXECFLEX_AI_JD_GENERATOR": jd_generator_enabled,
            "EXECFLEX_AI_QUESTION_FLOW": question_flow_enabled,
        }
        for var, fn in env_map.items():
            with patch.dict(os.environ, {var: "1"}, clear=True):
                assert fn() is True, f"{var} should enable {fn.__name__}"


# ─── agent service fallback ────────────────────────────────────────────────


class TestAgentServiceFallback:
    """When flags are off, agent_service functions return None."""

    def test_rerank_returns_none_when_disabled(self):
        from services.ai.agent_service import rerank_matches
        with patch.dict(os.environ, {}, clear=True):
            result = rerank_matches(
                job={"title": "Engineer"},
                candidates=[{"candidate_id": "c1"}],
            )
            assert result is None

    def test_screening_summary_returns_none_when_disabled(self):
        from services.ai.agent_service import summarise_screening
        with patch.dict(os.environ, {}, clear=True):
            result = summarise_screening(
                candidate_name="Test",
                role_title="Engineer",
                transcript=[{"question": "Q", "answer": "A"}],
            )
            assert result is None

    def test_cv_parser_returns_none_when_disabled(self):
        from services.ai.agent_service import parse_cv
        with patch.dict(os.environ, {}, clear=True):
            result = parse_cv(cv_text="Long CV text " * 20)
            assert result is None

    def test_jd_generator_returns_none_when_disabled(self):
        from services.ai.agent_service import generate_jd
        with patch.dict(os.environ, {}, clear=True):
            result = generate_jd(
                role_title="Engineer",
                company_summary="Test Corp",
                responsibilities="Build stuff",
                requirements="5 years",
                pay_range_min=80000,
                pay_range_max=120000,
                pay_currency="EUR",
                location="Dublin",
            )
            assert result is None

    def test_question_flow_returns_none_when_disabled(self):
        from services.ai.agent_service import get_question_flow_data
        with patch.dict(os.environ, {}, clear=True):
            result = get_question_flow_data("technology")
            assert result is None

    def test_question_flow_returns_data_when_enabled(self):
        from services.ai.agent_service import get_question_flow_data
        with patch.dict(os.environ, {"EXECFLEX_AI_QUESTION_FLOW": "1"}):
            result = get_question_flow_data("technology")
            assert result is not None
            assert result["role_type"] == "technology"
            assert len(result["questions"]) == 5

    def test_question_flow_falls_back_to_general(self):
        from services.ai.agent_service import get_question_flow_data
        with patch.dict(os.environ, {"EXECFLEX_AI_QUESTION_FLOW": "1"}):
            result = get_question_flow_data("unknown_type")
            assert result is not None
            assert result["role_type"] == "general"


# ─── agent service with mock LLM ────────────────────────────────────────────


class TestAgentServiceWithMock:
    """Test agent service with mocked agentic-core imports."""

    def test_rerank_returns_none_when_no_api_key(self):
        """When ANTHROPIC_API_KEY is missing, graceful fallback."""
        import services.ai.agent_service as svc
        svc._llm_client = None  # Reset lazy singleton

        with patch.dict(os.environ, {"EXECFLEX_AI_MATCH_RERANK": "1"}, clear=True):
            result = svc.rerank_matches(
                job={"title": "Engineer"},
                candidates=[{"candidate_id": "c1"}],
            )
            assert result is None

        svc._llm_client = None  # Clean up
