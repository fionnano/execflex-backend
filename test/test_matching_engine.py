"""
Matching Engine v1 — synthetic test suite.
50 synthetic candidates, 20 synthetic roles, deterministic scoring.
Zero real data. All fixtures are invented.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from services.matching.models import Candidate, Role, DimensionScore, MatchExplanation, MatchResult
from services.matching.engine import MatchEngine, DEFAULT_WEIGHTS


# ── Synthetic Candidates (50) ──────────────────────────────────────────

def _c(id, name, headline="", industries=None, skills=None, exp=0, loc="",
       avail="", comp_min=0, comp_max=0, ned=False, screening_rec="",
       screening_score=0.0, open_to="", pref_role=""):
    return Candidate(
        id=id, name=name, headline=headline,
        industries=set(industries or []), skills=set(skills or []),
        experience_years=exp, location=loc, availability=avail,
        compensation_min=comp_min, compensation_max=comp_max,
        is_ned_available=ned, screening_recommendation=screening_rec,
        screening_score=screening_score, open_to_opportunities=open_to,
        preferred_role_type=pref_role,
    )

CANDIDATES = [
    _c("c01", "Alice Chen", "CFO, Fintech", ["fintech", "banking"], ["finance", "strategy", "m&a"], 20, "london", "full_time", 180000, 220000, True, "strong_proceed", 4.5, "active", "full_time"),
    _c("c02", "Bob Murphy", "CTO", ["saas", "technology"], ["python", "architecture", "cloud"], 15, "dublin", "full_time", 150000, 200000, False, "proceed", 3.8, "active", "full_time"),
    _c("c03", "Claire Dubois", "VP Marketing", ["retail", "ecommerce"], ["marketing", "brand", "growth"], 12, "paris", "contract", 120000, 150000, False, "", 0.0, "active", "contract"),
    _c("c04", "Dan O'Brien", "Head of Sales", ["saas", "technology"], ["sales", "enterprise", "partnerships"], 10, "london", "full_time", 130000, 160000, False, "proceed", 3.5, "active", "full_time"),
    _c("c05", "Elena Rossi", "COO", ["manufacturing", "logistics"], ["operations", "supply chain", "lean"], 18, "milan", "full_time", 160000, 200000, True, "strong_proceed", 4.8, "active", "full_time"),
    _c("c06", "Frank Weber", "Data Scientist", ["fintech", "insurance"], ["machine learning", "python", "statistics"], 8, "berlin", "contract", 90000, 120000, False, "", 0.0, "passive", "contract"),
    _c("c07", "Grace Kim", "Product Manager", ["saas", "healthtech"], ["product", "agile", "roadmap"], 7, "san francisco", "full_time", 140000, 170000, False, "proceed", 3.2, "active", "full_time"),
    _c("c08", "Harry Singh", "Engineering Manager", ["fintech", "payments"], ["engineering", "leadership", "python"], 12, "london", "full_time", 140000, 180000, False, "strong_proceed", 4.2, "active", "full_time"),
    _c("c09", "Irene Costa", "CHRO", ["pharma", "biotech"], ["hr", "talent", "culture", "strategy"], 16, "lisbon", "full_time", 150000, 190000, True, "proceed", 3.9, "active", "full_time"),
    _c("c10", "James Walsh", "Fractional CFO", ["saas", "fintech"], ["finance", "fundraising", "board"], 22, "london", "fractional", 100000, 150000, True, "strong_proceed", 4.6, "active", "fractional"),
    _c("c11", "Kate Yilmaz", "Frontend Developer", ["technology", "ecommerce"], ["react", "typescript", "css"], 5, "istanbul", "remote", 50000, 70000, False, "", 0.0, "active", "full_time"),
    _c("c12", "Liam Patel", "Backend Developer", ["fintech", "saas"], ["python", "django", "postgresql"], 6, "mumbai", "remote", 40000, 60000, False, "", 0.0, "active", "full_time"),
    _c("c13", "Mei Zhang", "AI Research Lead", ["technology", "ai"], ["deep learning", "nlp", "python", "research"], 10, "beijing", "full_time", 200000, 250000, False, "strong_proceed", 4.7, "active", "full_time"),
    _c("c14", "Noah Eriksson", "UX Designer", ["saas", "healthtech"], ["ux", "figma", "user research"], 4, "stockholm", "contract", 60000, 80000, False, "", 0.0, "active", "contract"),
    _c("c15", "Olivia Brown", "General Counsel", ["fintech", "banking"], ["legal", "compliance", "regulation"], 14, "new york", "full_time", 200000, 280000, False, "proceed", 3.6, "active", "full_time"),
    _c("c16", "Patrick O'Leary", "VP Engineering", ["saas", "technology"], ["engineering", "leadership", "architecture", "cloud"], 16, "dublin", "full_time", 170000, 210000, False, "strong_proceed", 4.4, "active", "full_time"),
    _c("c17", "Quinn Roberts", "Sustainability Lead", ["energy", "cleantech"], ["sustainability", "esg", "reporting"], 8, "london", "full_time", 90000, 120000, False, "", 0.0, "active", "full_time"),
    _c("c18", "Rachel Nguyen", "Growth Marketer", ["saas", "ecommerce"], ["growth", "paid media", "analytics"], 6, "ho chi minh city", "remote", 50000, 70000, False, "", 0.0, "active", "full_time"),
    _c("c19", "Sam Johansson", "DevOps Engineer", ["technology", "fintech"], ["kubernetes", "aws", "terraform", "ci/cd"], 9, "stockholm", "contract", 100000, 130000, False, "proceed", 3.4, "active", "contract"),
    _c("c20", "Tara Malik", "Head of People", ["technology", "saas"], ["hr", "talent acquisition", "culture"], 11, "london", "full_time", 110000, 140000, False, "proceed", 3.7, "active", "full_time"),
    _c("c21", "Uma Reddy", "CEO", ["healthtech", "biotech"], ["leadership", "strategy", "fundraising", "board"], 25, "bangalore", "full_time", 250000, 350000, True, "strong_proceed", 4.9, "active", "full_time"),
    _c("c22", "Victor Petrov", "Security Engineer", ["fintech", "banking"], ["security", "penetration testing", "compliance"], 7, "moscow", "remote", 80000, 110000, False, "", 0.0, "passive", "full_time"),
    _c("c23", "Wendy Taylor", "Content Strategist", ["media", "saas"], ["content", "seo", "copywriting"], 5, "sydney", "part_time", 40000, 55000, False, "", 0.0, "active", "part_time"),
    _c("c24", "Xander Liu", "Quantitative Analyst", ["fintech", "hedge funds"], ["quantitative analysis", "python", "statistics", "risk"], 9, "hong kong", "full_time", 160000, 220000, False, "proceed", 3.8, "active", "full_time"),
    _c("c25", "Yuki Tanaka", "Supply Chain Director", ["manufacturing", "automotive"], ["supply chain", "logistics", "procurement", "lean"], 14, "tokyo", "full_time", 140000, 180000, False, "strong_proceed", 4.3, "active", "full_time"),
    _c("c26", "Zara Ahmed", "NED/iNED", ["governance", "fintech"], ["board", "governance", "audit", "risk"], 30, "london", "fractional", 50000, 80000, True, "strong_proceed", 4.8, "active", "fractional"),
    _c("c27", "Adam Foster", "Interim CTO", ["saas", "startup"], ["architecture", "cloud", "leadership", "python"], 18, "remote", "interim", 150000, 200000, False, "strong_proceed", 4.5, "active", "interim"),
    _c("c28", "Beth Clarke", "HR Business Partner", ["retail", "hospitality"], ["hr", "employee relations", "performance"], 6, "manchester", "full_time", 55000, 70000, False, "", 0.0, "active", "full_time"),
    _c("c29", "Charlie Dunn", "Data Engineer", ["technology", "saas"], ["python", "spark", "airflow", "postgresql"], 4, "london", "full_time", 65000, 85000, False, "", 0.0, "active", "full_time"),
    _c("c30", "Diana Morales", "VP Sales EMEA", ["saas", "enterprise software"], ["sales", "enterprise", "emea", "partnerships"], 13, "madrid", "full_time", 150000, 190000, False, "proceed", 3.6, "active", "full_time"),
    _c("c31", "Edward Ng", "Infrastructure Architect", ["technology", "cloud"], ["aws", "gcp", "architecture", "security"], 11, "singapore", "full_time", 130000, 170000, False, "proceed", 3.5, "active", "full_time"),
    _c("c32", "Fiona McCarthy", "Regulatory Affairs Manager", ["pharma", "biotech"], ["regulation", "compliance", "fda", "ema"], 9, "cork", "full_time", 80000, 100000, False, "", 0.0, "active", "full_time"),
    _c("c33", "George Papadopoulos", "CFO", ["shipping", "logistics"], ["finance", "treasury", "m&a", "board"], 20, "athens", "full_time", 170000, 220000, True, "strong_proceed", 4.4, "active", "full_time"),
    _c("c34", "Hannah Lee", "Product Designer", ["saas", "fintech"], ["design", "figma", "user research", "prototyping"], 6, "seoul", "remote", 70000, 90000, False, "proceed", 3.3, "active", "full_time"),
    _c("c35", "Ivan Kozlov", "ML Engineer", ["ai", "technology"], ["machine learning", "python", "tensorflow", "mlops"], 7, "tallinn", "full_time", 90000, 120000, False, "", 0.0, "active", "full_time"),
    _c("c36", "Julia Fernandes", "Country Manager", ["consumer goods", "retail"], ["general management", "p&l", "strategy"], 15, "sao paulo", "full_time", 140000, 180000, False, "proceed", 3.7, "active", "full_time"),
    _c("c37", "Karl Schmidt", "Compliance Officer", ["banking", "fintech"], ["compliance", "aml", "kyc", "regulation"], 10, "frankfurt", "full_time", 100000, 130000, False, "proceed", 3.5, "active", "full_time"),
    _c("c38", "Lisa Andersson", "Talent Acquisition Lead", ["technology", "saas"], ["talent acquisition", "employer brand", "recruitment"], 8, "stockholm", "full_time", 80000, 100000, False, "", 0.0, "active", "full_time"),
    _c("c39", "Michael O'Connor", "Commercial Director", ["energy", "utilities"], ["commercial", "strategy", "partnerships", "b2b"], 14, "dublin", "full_time", 130000, 170000, False, "proceed", 3.6, "active", "full_time"),
    _c("c40", "Nina Petrova", "QA Lead", ["technology", "saas"], ["testing", "automation", "quality"], 6, "sofia", "remote", 45000, 60000, False, "", 0.0, "active", "full_time"),
    _c("c41", "Oscar Reyes", "Fullstack Developer", ["technology", "ecommerce"], ["react", "node", "python", "postgresql"], 5, "mexico city", "remote", 50000, 70000, False, "", 0.0, "active", "full_time"),
    _c("c42", "Priya Sharma", "Chief People Officer", ["technology", "saas"], ["hr", "culture", "talent", "d&i", "strategy"], 17, "london", "full_time", 180000, 230000, True, "strong_proceed", 4.6, "active", "full_time"),
    _c("c43", "Richard Hughes", "Board Advisor", ["private equity", "fintech"], ["board", "governance", "m&a", "fundraising"], 28, "london", "fractional", 60000, 100000, True, "strong_proceed", 4.7, "active", "fractional"),
    _c("c44", "Sophie Martin", "VP Customer Success", ["saas", "technology"], ["customer success", "retention", "expansion", "leadership"], 10, "london", "full_time", 120000, 150000, False, "proceed", 3.8, "active", "full_time"),
    _c("c45", "Thomas Berg", "Principal Consultant", ["consulting", "strategy"], ["strategy", "transformation", "change management"], 12, "oslo", "contract", 110000, 140000, False, "", 0.0, "active", "contract"),
    _c("c46", "Ursula Klein", "Creative Director", ["media", "advertising"], ["creative", "brand", "design", "campaigns"], 13, "hamburg", "full_time", 100000, 130000, False, "", 0.0, "active", "full_time"),
    _c("c47", "Vincent Tran", "Embedded Engineer", ["automotive", "iot"], ["embedded", "c++", "firmware", "rtos"], 8, "detroit", "full_time", 110000, 140000, False, "", 0.0, "active", "full_time"),
    _c("c48", "Wanda Osei", "Programme Manager", ["ngo", "government"], ["programme management", "stakeholder", "governance"], 11, "accra", "full_time", 70000, 90000, False, "proceed", 3.4, "active", "full_time"),
    _c("c49", "Xavier Dupont", "Closed to opportunities", ["fintech"], ["finance"], 15, "paris", "full_time", 160000, 200000, False, "", 0.0, "no", "full_time"),
    _c("c50", "Yara Benali", "Junior Analyst", ["consulting"], ["excel", "analysis"], 1, "casablanca", "full_time", 25000, 35000, False, "", 0.0, "active", "full_time"),
]


# ── Synthetic Roles (20) ──────────────────────────────────────────────

def _r(id, title, industry="", skills=None, min_exp=0, loc="",
       commitment="", budget_min=0, budget_max=0, ned=False, desc=""):
    return Role(
        id=id, title=title, industry=industry,
        required_skills=set(skills or []), min_experience=min_exp,
        location=loc, commitment_type=commitment,
        budget_min=budget_min, budget_max=budget_max,
        is_ned=ned, description=desc,
    )

ROLES = [
    _r("r01", "CFO", "fintech", ["finance", "strategy", "m&a"], 15, "london", "full_time", 180000, 250000),
    _r("r02", "CTO", "saas", ["architecture", "cloud", "python", "leadership"], 10, "dublin", "full_time", 150000, 220000),
    _r("r03", "VP Marketing", "ecommerce", ["marketing", "growth", "brand"], 8, "remote", "full_time", 100000, 150000),
    _r("r04", "Head of Sales", "saas", ["sales", "enterprise", "partnerships"], 8, "london", "full_time", 120000, 170000),
    _r("r05", "COO", "manufacturing", ["operations", "supply chain", "lean"], 12, "milan", "full_time", 150000, 200000),
    _r("r06", "Data Scientist", "fintech", ["machine learning", "python", "statistics"], 5, "berlin", "contract", 80000, 130000),
    _r("r07", "Product Manager", "healthtech", ["product", "agile", "roadmap"], 5, "san francisco", "full_time", 120000, 160000),
    _r("r08", "Engineering Manager", "fintech", ["engineering", "leadership", "python"], 8, "london", "full_time", 130000, 180000),
    _r("r09", "CHRO", "biotech", ["hr", "talent", "culture"], 12, "remote", "full_time", 140000, 200000),
    _r("r10", "Fractional CFO", "saas", ["finance", "fundraising", "board"], 15, "london", "fractional", 80000, 150000),
    _r("r11", "NED — Fintech", "fintech", ["board", "governance", "risk"], 20, "london", "fractional", 40000, 80000, ned=True),
    _r("r12", "Senior Backend Developer", "saas", ["python", "django", "postgresql"], 4, "remote", "full_time", 60000, 100000),
    _r("r13", "AI Research Lead", "ai", ["deep learning", "nlp", "python"], 8, "remote", "full_time", 180000, 260000),
    _r("r14", "UX Designer", "saas", ["ux", "figma", "user research"], 3, "remote", "contract", 50000, 80000),
    _r("r15", "VP Engineering", "saas", ["engineering", "leadership", "architecture"], 12, "dublin", "full_time", 160000, 220000),
    _r("r16", "DevOps Engineer", "fintech", ["kubernetes", "aws", "terraform"], 5, "remote", "contract", 80000, 140000),
    _r("r17", "Head of People", "technology", ["hr", "talent acquisition", "culture"], 8, "london", "full_time", 100000, 150000),
    _r("r18", "General Counsel", "fintech", ["legal", "compliance", "regulation"], 10, "new york", "full_time", 180000, 280000),
    _r("r19", "Country Manager LATAM", "consumer goods", ["general management", "p&l", "strategy"], 10, "sao paulo", "full_time", 120000, 180000),
    _r("r20", "Compliance Officer", "banking", ["compliance", "aml", "kyc"], 7, "frankfurt", "full_time", 90000, 140000),
]


# ── Tests ──────────────────────────────────────────────────────────────

class TestModels:
    def test_candidate_normalizes_sets(self):
        c = Candidate(id="t1", name="Test", industries=["FinTech", "BANKING"], skills=["Python"])
        assert "fintech" in c.industries
        assert "banking" in c.industries
        assert "python" in c.skills

    def test_role_normalizes_sets(self):
        r = Role(id="t1", title="Test", required_skills=["Python", "Django"])
        assert "python" in r.required_skills
        assert "django" in r.required_skills

    def test_match_result_score_property(self):
        c = Candidate(id="t1", name="Test")
        expl = MatchExplanation(dimension_scores={}, composite_score=75.5, summary="test")
        mr = MatchResult(candidate=c, explanation=expl)
        assert mr.score == 75.5


class TestDimensionScoring:
    def setup_method(self):
        self.engine = MatchEngine()

    def test_industry_exact_match(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])  # Alice (fintech) vs CFO (fintech)
        industry = result.explanation.dimension_scores["industry_fit"]
        assert industry.score >= 50.0

    def test_industry_no_overlap(self):
        result = self.engine.score_candidate(CANDIDATES[46], ROLES[0])  # Vincent (automotive) vs CFO (fintech)
        industry = result.explanation.dimension_scores["industry_fit"]
        assert industry.score == 0.0

    def test_skills_full_match(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])  # Alice has finance, strategy, m&a
        skills = result.explanation.dimension_scores["skills_fit"]
        assert skills.score >= 80.0

    def test_skills_partial_match(self):
        result = self.engine.score_candidate(CANDIDATES[1], ROLES[0])  # Bob (python, arch, cloud) vs CFO
        skills = result.explanation.dimension_scores["skills_fit"]
        assert skills.score < 50.0

    def test_experience_exceeds(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])  # Alice 20yr vs 15yr req
        exp = result.explanation.dimension_scores["experience_fit"]
        assert exp.score >= 90.0

    def test_experience_below(self):
        result = self.engine.score_candidate(CANDIDATES[49], ROLES[0])  # Yara 1yr vs 15yr req
        exp = result.explanation.dimension_scores["experience_fit"]
        assert exp.score < 20.0

    def test_location_exact_match(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])  # London vs London
        loc = result.explanation.dimension_scores["location_fit"]
        assert loc.score >= 80.0

    def test_location_mismatch(self):
        result = self.engine.score_candidate(CANDIDATES[12], ROLES[0])  # Beijing vs London
        loc = result.explanation.dimension_scores["location_fit"]
        assert loc.score < 50.0

    def test_remote_availability_matches(self):
        result = self.engine.score_candidate(CANDIDATES[10], ROLES[2])  # Kate (istanbul, remote) vs VP Mktg (remote)
        loc = result.explanation.dimension_scores["location_fit"]
        assert loc.score >= 80.0

    def test_compensation_within_budget(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])  # 180k-220k vs 180k-250k budget
        comp = result.explanation.dimension_scores["compensation_fit"]
        assert comp.score >= 80.0

    def test_compensation_above_budget(self):
        over_budget = Candidate(
            id="over", name="Over Budget", skills={"finance"},
            experience_years=20, compensation_min=300000, compensation_max=400000,
            open_to_opportunities="active",
        )
        result = self.engine.score_candidate(over_budget, ROLES[0])  # 300k-400k vs 180k-250k budget
        comp = result.explanation.dimension_scores["compensation_fit"]
        assert comp.score < 80.0

    def test_screening_strong_proceed(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])  # Alice strong_proceed
        scr = result.explanation.dimension_scores["screening_fit"]
        assert scr.score == 100.0

    def test_screening_not_screened(self):
        result = self.engine.score_candidate(CANDIDATES[10], ROLES[0])  # Kate not screened
        scr = result.explanation.dimension_scores["screening_fit"]
        assert scr.score == 50.0


class TestCompositeScoring:
    def setup_method(self):
        self.engine = MatchEngine()

    def test_perfect_match_scores_high(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])  # Alice vs CFO
        assert result.score >= 70.0

    def test_poor_match_scores_low(self):
        result = self.engine.score_candidate(CANDIDATES[49], ROLES[0])  # Junior analyst vs CFO
        assert result.score < 30.0

    def test_ned_filter_penalizes_non_ned(self):
        result = self.engine.score_candidate(CANDIDATES[1], ROLES[10])  # Bob (not NED) vs NED role
        assert result.score < 30.0
        assert "ned_filter" in result.explanation.dimension_scores

    def test_ned_filter_passes_ned_candidate(self):
        result = self.engine.score_candidate(CANDIDATES[25], ROLES[10])  # Zara (NED) vs NED role
        assert "ned_filter" not in result.explanation.dimension_scores

    def test_closed_candidate_penalized(self):
        result = self.engine.score_candidate(CANDIDATES[48], ROLES[0])  # Xavier (no opp) vs CFO
        assert result.score < 10.0
        assert "openness" in result.explanation.dimension_scores

    def test_passive_candidate_slight_penalty(self):
        result_passive = self.engine.score_candidate(CANDIDATES[5], ROLES[5])  # Frank (passive) vs Data Sci
        result_active = self.engine.score_candidate(CANDIDATES[5].__class__(
            id="c06a", name="Frank Active", headline=CANDIDATES[5].headline,
            industries=CANDIDATES[5].industries, skills=CANDIDATES[5].skills,
            experience_years=CANDIDATES[5].experience_years, location=CANDIDATES[5].location,
            availability=CANDIDATES[5].availability, compensation_min=CANDIDATES[5].compensation_min,
            compensation_max=CANDIDATES[5].compensation_max, open_to_opportunities="active"
        ), ROLES[5])
        assert result_passive.score < result_active.score


class TestExplanation:
    def setup_method(self):
        self.engine = MatchEngine()

    def test_explanation_has_all_dimensions(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])
        dims = result.explanation.dimension_scores
        for key in ["industry_fit", "skills_fit", "experience_fit", "location_fit",
                     "availability_fit", "compensation_fit", "screening_fit"]:
            assert key in dims, f"Missing dimension: {key}"
            assert isinstance(dims[key], DimensionScore)
            assert dims[key].reason, f"Empty reason for {key}"

    def test_summary_contains_score(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])
        assert "score:" in result.explanation.summary
        assert "/100" in result.explanation.summary

    def test_summary_labels_strong_match(self):
        result = self.engine.score_candidate(CANDIDATES[0], ROLES[0])
        if result.score >= 75:
            assert "Strong match" in result.explanation.summary

    def test_summary_labels_weak_match(self):
        result = self.engine.score_candidate(CANDIDATES[49], ROLES[0])
        if result.score < 50:
            assert "Weak match" in result.explanation.summary or "Moderate match" in result.explanation.summary


class TestMatchEngineRanking:
    def setup_method(self):
        self.engine = MatchEngine()

    def test_match_returns_sorted_results(self):
        results = self.engine.match(CANDIDATES, ROLES[0], limit=10)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_match_respects_limit(self):
        results = self.engine.match(CANDIDATES, ROLES[0], limit=5)
        assert len(results) <= 5

    def test_match_assigns_ranks(self):
        results = self.engine.match(CANDIDATES, ROLES[0], limit=10)
        for i, r in enumerate(results):
            assert r.rank == i + 1

    def test_match_min_score_filter(self):
        results = self.engine.match(CANDIDATES, ROLES[0], min_score=50.0)
        for r in results:
            assert r.score >= 50.0

    def test_best_candidate_for_cfo_is_alice(self):
        results = self.engine.match(CANDIDATES, ROLES[0], limit=5)
        assert results[0].candidate.id == "c01"

    def test_best_candidate_for_cto_is_bob_or_patrick(self):
        results = self.engine.match(CANDIDATES, ROLES[1], limit=3)
        top_ids = [r.candidate.id for r in results]
        assert "c02" in top_ids or "c16" in top_ids

    def test_ned_role_returns_ned_candidates_first(self):
        results = self.engine.match(CANDIDATES, ROLES[10], limit=5)
        assert results[0].candidate.is_ned_available

    def test_fractional_role_matches_fractional_candidates(self):
        results = self.engine.match(CANDIDATES, ROLES[9], limit=5)
        top = results[0]
        assert top.candidate.id in ("c10", "c43", "c26")

    def test_all_20_roles_produce_results(self):
        for role in ROLES:
            results = self.engine.match(CANDIDATES, role, limit=5)
            assert len(results) > 0, f"No results for role {role.id} ({role.title})"

    def test_closed_candidate_never_ranks_top5(self):
        for role in ROLES:
            results = self.engine.match(CANDIDATES, role, limit=5)
            top_ids = [r.candidate.id for r in results]
            assert "c49" not in top_ids, f"Closed candidate c49 ranked top 5 for {role.id}"


class TestCustomWeights:
    def test_skills_heavy_weighting(self):
        engine = MatchEngine(weights={
            "industry_fit": 0.05, "skills_fit": 0.60, "experience_fit": 0.10,
            "location_fit": 0.05, "availability_fit": 0.05, "compensation_fit": 0.05,
            "screening_fit": 0.10,
        })
        results = engine.match(CANDIDATES, ROLES[0], limit=5)
        assert len(results) > 0

    def test_weights_auto_normalize(self):
        engine = MatchEngine(weights={
            "industry_fit": 2.0, "skills_fit": 3.0, "experience_fit": 1.0,
            "location_fit": 1.0, "availability_fit": 1.0, "compensation_fit": 1.0,
            "screening_fit": 1.0,
        })
        total = sum(engine.weights.values())
        assert abs(total - 1.0) < 0.01


class TestEdgeCases:
    def setup_method(self):
        self.engine = MatchEngine()

    def test_empty_candidate_list(self):
        results = self.engine.match([], ROLES[0])
        assert results == []

    def test_candidate_with_no_data(self):
        empty = Candidate(id="empty", name="Empty Person")
        result = self.engine.score_candidate(empty, ROLES[0])
        assert result.score >= 0.0
        assert result.score <= 100.0

    def test_role_with_no_requirements(self):
        easy_role = Role(id="easy", title="Open Role")
        result = self.engine.score_candidate(CANDIDATES[0], easy_role)
        assert result.score >= 40.0  # All dimensions default to 50 when no requirement

    def test_score_always_in_range(self):
        for c in CANDIDATES:
            for r in ROLES:
                result = self.engine.score_candidate(c, r)
                assert 0.0 <= result.score <= 100.0, \
                    f"Score {result.score} out of range for {c.id} vs {r.id}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
