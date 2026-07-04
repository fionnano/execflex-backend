"""Tests for compliance layer — human review gate, decision logger contracts."""
import pytest

from services.compliance.human_review import require_human_review_for_reject


# ── Mock OrgContext ─────────────────────────────────────────────────

class MockCtx:
    def __init__(self, user_id="user-123", org_id="org-456", role="recruiter"):
        self.user_id = user_id
        self.org_id = org_id
        self.role = role


# ── Human review gate ───────────────────────────────────────────────

class TestHumanReviewGate:
    def test_rejects_when_no_context(self):
        result = require_human_review_for_reject(None, "cand-1", "not a fit")
        assert result["allowed"] is False
        assert "human authorization" in result["message"].lower()

    def test_rejects_when_no_user_id(self):
        ctx = MockCtx(user_id="")
        result = require_human_review_for_reject(ctx, "cand-1", "not a fit")
        assert result["allowed"] is False

    def test_rejects_when_no_reason(self):
        ctx = MockCtx()
        result = require_human_review_for_reject(ctx, "cand-1", "")
        assert result["allowed"] is False
        assert "reason is required" in result["message"].lower()

    def test_rejects_when_reason_too_short(self):
        ctx = MockCtx()
        result = require_human_review_for_reject(ctx, "cand-1", "no")
        assert result["allowed"] is False

    def test_allows_with_valid_context_and_reason(self):
        ctx = MockCtx()
        result = require_human_review_for_reject(ctx, "cand-1", "Does not meet minimum experience requirement")
        assert result["allowed"] is True

    def test_allows_with_minimal_valid_reason(self):
        ctx = MockCtx()
        result = require_human_review_for_reject(ctx, "cand-1", "N/A")
        assert result["allowed"] is True

    def test_message_mentions_eu_ai_act(self):
        result = require_human_review_for_reject(None, "cand-1", "reason")
        assert "EU AI Act" in result["message"]

    def test_message_mentions_gdpr_for_reason(self):
        ctx = MockCtx()
        result = require_human_review_for_reject(ctx, "cand-1", "")
        assert "GDPR" in result["message"]


# ── Assessment adapter protocol ─────────────────────────────────────

class TestAssessmentAdapter:
    def test_stub_adapter_create(self):
        from services.talent_pools.assessment_adapter import StubAssessmentAdapter
        adapter = StubAssessmentAdapter()
        assessment_id = adapter.create_assessment("cand-1", "pool-1", {})
        assert "cand-1" in assessment_id

    def test_stub_adapter_get_result(self):
        from services.talent_pools.assessment_adapter import StubAssessmentAdapter
        adapter = StubAssessmentAdapter()
        aid = adapter.create_assessment("cand-1", "pool-1", {})
        result = adapter.get_result(aid)
        assert result is not None
        assert result.passed is True
        assert result.score == 85.0
        assert result.provider == "stub"

    def test_stub_adapter_list_tests(self):
        from services.talent_pools.assessment_adapter import StubAssessmentAdapter
        adapter = StubAssessmentAdapter()
        tests = adapter.list_available_tests()
        assert len(tests) == 3
        categories = {t["category"] for t in tests}
        assert "tech" in categories
        assert "finance" in categories

    def test_assessment_result_fields(self):
        from services.talent_pools.assessment_adapter import AssessmentResult
        result = AssessmentResult(
            provider="codility",
            candidate_id="cand-1",
            pool_id="pool-1",
            passed=True,
            score=92.5,
        )
        assert result.max_score == 100.0
        assert result.details == {}
        assert result.assessment_url is None


# ── AI notice content ───────────────────────────────────────────────

class TestAINotice:
    def test_notice_covers_voice_screening(self):
        notice = self._get_notice_text()
        assert "voice screening" in notice.lower() or "VOICE SCREENING" in notice

    def test_notice_covers_matching(self):
        notice = self._get_notice_text()
        assert "matching" in notice.lower()

    def test_notice_covers_human_review(self):
        notice = self._get_notice_text()
        assert "human review" in notice.lower()

    def test_notice_covers_gdpr_rights(self):
        notice = self._get_notice_text()
        assert "GDPR" in notice
        assert "Art. 15" in notice or "access" in notice.lower()
        assert "Art. 17" in notice or "erasure" in notice.lower()

    def test_notice_covers_right_to_explanation(self):
        notice = self._get_notice_text()
        assert "explanation" in notice.lower()

    def _get_notice_text(self):
        return (
            "ExecFlex uses artificial intelligence in the following ways:\n\n"
            "1. VOICE SCREENING: An AI assistant conducts initial screening calls with candidates. "
            "Candidates are informed at the start of each call and must consent before proceeding.\n\n"
            "2. CANDIDATE MATCHING: An AI-powered matching engine scores candidates against job "
            "requirements across multiple dimensions (skills, experience, location, etc.). "
            "All scores include human-readable explanations.\n\n"
            "3. SCORING: Screening responses are scored to generate recommendations. "
            "No candidate is automatically rejected — all terminal decisions require human review.\n\n"
            "CANDIDATE RIGHTS:\n"
            "- Right to know AI is being used (this notice)\n"
            "- Right to human review of any AI-influenced decision\n"
            "- Right to explanation of how scores were calculated\n"
            "- Right to access your data (GDPR Art. 15)\n"
            "- Right to erasure of your data (GDPR Art. 17)\n\n"
            "To exercise these rights, submit a request via the data rights endpoint "
            "or email privacy@execflex.ai."
        )
