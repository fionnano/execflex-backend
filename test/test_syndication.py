"""Tests for job syndication engine and adapters — synthetic data only."""
import json
import xml.etree.ElementTree as ET

import pytest

from services.syndication.adapters import (
    GenericXMLAdapter,
    GoogleIndexingStubAdapter,
    IndeedXMLAdapter,
    IrishJobsXMLAdapter,
    JobPosting,
    LinkedInXMLAdapter,
    SyndicationResult,
)
from services.syndication.engine import SyndicationEngine


# ── Synthetic job fixtures ──────────────────────────────────────────

def _make_job(**overrides) -> JobPosting:
    defaults = {
        "id": "job-001-test",
        "title": "Senior Software Engineer",
        "description": "Build scalable systems for fintech platform.",
        "location": "Dublin, Ireland",
        "company_name": "Acme Recruiting",
        "pay_range_min": 80000,
        "pay_range_max": 120000,
        "pay_currency": "EUR",
        "pay_period": "annual",
        "commitment_type": "full-time",
        "industry": "Technology",
        "is_remote": True,
        "apply_url": "https://execflex.ai/apply/job-001-test",
        "posted_at": "2026-07-04T00:00:00Z",
    }
    defaults.update(overrides)
    return JobPosting(**defaults)


JOBS = [
    _make_job(),
    _make_job(id="job-002-test", title="CFO", location="London, UK", industry="Finance",
              pay_range_min=150000, pay_range_max=250000, is_remote=False),
    _make_job(id="job-003-test", title="VP Marketing", location="Cork, Ireland",
              commitment_type="contract", pay_range_min=90000, pay_range_max=130000),
]


# ── LinkedIn adapter ────────────────────────────────────────────────

class TestLinkedInAdapter:
    def setup_method(self):
        self.adapter = LinkedInXMLAdapter()

    def test_board_name(self):
        assert self.adapter.board_name == "linkedin"

    def test_submit_returns_success(self):
        result = self.adapter.submit(JOBS[0])
        assert result.success is True
        assert result.board == "linkedin"
        assert result.external_id.startswith("li-")
        assert result.feed_xml is not None

    def test_submit_generates_valid_xml(self):
        result = self.adapter.submit(JOBS[0])
        root = ET.fromstring(result.feed_xml)
        assert root.tag == "source"
        assert root.find("publisher").text == "ExecFlex"
        jobs = root.findall("job")
        assert len(jobs) == 1
        assert jobs[0].find("title").text == "Senior Software Engineer"

    def test_feed_contains_salary(self):
        result = self.adapter.submit(JOBS[0])
        root = ET.fromstring(result.feed_xml)
        job = root.find("job")
        salary = job.find("salary")
        assert salary is not None
        assert salary.find("min").text == "80000"
        assert salary.find("max").text == "120000"
        assert salary.find("currency").text == "EUR"

    def test_multi_job_feed(self):
        feed = self.adapter.generate_feed(JOBS)
        root = ET.fromstring(feed)
        assert len(root.findall("job")) == 3

    def test_remove_returns_success(self):
        result = self.adapter.remove("li-abc123")
        assert result.success is True
        assert result.external_id == "li-abc123"

    def test_city_extraction(self):
        result = self.adapter.submit(JOBS[0])
        root = ET.fromstring(result.feed_xml)
        job = root.find("job")
        city = job.find("city")
        assert city is not None
        assert city.text == "Dublin"


# ── Indeed adapter ──────────────────────────────────────────────────

class TestIndeedAdapter:
    def setup_method(self):
        self.adapter = IndeedXMLAdapter()

    def test_board_name(self):
        assert self.adapter.board_name == "indeed"

    def test_submit_returns_success(self):
        result = self.adapter.submit(JOBS[0])
        assert result.success is True
        assert result.external_id.startswith("ind-")

    def test_feed_has_referencenumber(self):
        result = self.adapter.submit(JOBS[0])
        root = ET.fromstring(result.feed_xml)
        job = root.find("job")
        ref = job.find("referencenumber")
        assert ref is not None
        assert ref.text == "job-001-test"

    def test_feed_has_sourcename(self):
        result = self.adapter.submit(JOBS[0])
        root = ET.fromstring(result.feed_xml)
        job = root.find("job")
        assert job.find("sourcename").text == "ExecFlex"


# ── IrishJobs adapter ──────────────────────────────────────────────

class TestIrishJobsAdapter:
    def setup_method(self):
        self.adapter = IrishJobsXMLAdapter()

    def test_board_name(self):
        assert self.adapter.board_name == "irishjobs"

    def test_submit_returns_success(self):
        result = self.adapter.submit(JOBS[0])
        assert result.success is True
        assert result.external_id.startswith("ij-")

    def test_feed_has_region(self):
        feed = self.adapter.generate_feed(JOBS[:1])
        root = ET.fromstring(feed)
        job = root.find("job")
        assert job.find("region").text == "Ireland"

    def test_feed_root_tag(self):
        feed = self.adapter.generate_feed(JOBS[:1])
        root = ET.fromstring(feed)
        assert root.tag == "jobs"
        assert root.get("source") == "ExecFlex"


# ── Generic adapter ────────────────────────────────────────────────

class TestGenericAdapter:
    def test_custom_board_name(self):
        adapter = GenericXMLAdapter(board_name="monster")
        assert adapter.board_name == "monster"

    def test_submit_returns_correct_prefix(self):
        adapter = GenericXMLAdapter(board_name="reed")
        result = adapter.submit(JOBS[0])
        assert result.success is True
        assert result.external_id.startswith("gen-")
        assert result.board == "reed"

    def test_feed_has_board_attribute(self):
        adapter = GenericXMLAdapter(board_name="totaljobs")
        feed = adapter.generate_feed(JOBS[:1])
        root = ET.fromstring(feed)
        assert root.get("board") == "totaljobs"


# ── Google Indexing stub ────────────────────────────────────────────

class TestGoogleIndexingStub:
    def setup_method(self):
        self.adapter = GoogleIndexingStubAdapter()

    def test_board_name(self):
        assert self.adapter.board_name == "google_indexing"

    def test_submit_returns_json(self):
        result = self.adapter.submit(JOBS[0])
        assert result.success is True
        data = json.loads(result.feed_xml)
        assert data["type"] == "URL_UPDATED"
        assert "execflex.ai" in data["url"]

    def test_remove_returns_delete(self):
        result = self.adapter.remove("gidx-abc123")
        data = json.loads(result.feed_xml)
        assert data["type"] == "URL_DELETED"

    def test_feed_generates_entries(self):
        feed = self.adapter.generate_feed(JOBS)
        data = json.loads(feed)
        assert len(data["entries"]) == 3


# ── Syndication engine ──────────────────────────────────────────────

class TestSyndicationEngine:
    def setup_method(self):
        self.engine = SyndicationEngine()

    def test_available_boards(self):
        boards = self.engine.available_boards
        assert "linkedin" in boards
        assert "indeed" in boards
        assert "irishjobs" in boards
        assert "google_indexing" in boards

    def test_syndicate_single_board(self):
        results = self.engine.syndicate(JOBS[0], ["linkedin"])
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].board == "linkedin"

    def test_syndicate_multiple_boards(self):
        results = self.engine.syndicate(JOBS[0], ["linkedin", "indeed", "irishjobs"])
        assert len(results) == 3
        assert all(r.success for r in results)

    def test_syndicate_unknown_board_uses_generic(self):
        results = self.engine.syndicate(JOBS[0], ["totaljobs"])
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].board == "totaljobs"

    def test_remove_known_board(self):
        result = self.engine.remove("linkedin", "li-abc")
        assert result.success is True

    def test_remove_unknown_board(self):
        result = self.engine.remove("nonexistent", "x")
        assert result.success is False
        assert "Unknown board" in result.error

    def test_generate_feed(self):
        feed = self.engine.generate_feed("linkedin", JOBS)
        assert feed is not None
        root = ET.fromstring(feed)
        assert len(root.findall("job")) == 3

    def test_generate_feed_unknown_board(self):
        feed = self.engine.generate_feed("nonexistent", JOBS)
        assert feed is None

    def test_register_custom_adapter(self):
        custom = GenericXMLAdapter(board_name="custom_board")
        self.engine.register_adapter(custom)
        assert "custom_board" in self.engine.available_boards
        results = self.engine.syndicate(JOBS[0], ["custom_board"])
        assert results[0].success is True

    def test_all_jobs_produce_valid_feeds(self):
        for board in self.engine.available_boards:
            feed = self.engine.generate_feed(board, JOBS)
            assert feed is not None, f"No feed for {board}"


# ── Contract tests (feed structure) ─────────────────────────────────

class TestFeedContracts:
    """Every adapter must produce feeds with required fields."""

    ADAPTERS = [LinkedInXMLAdapter(), IndeedXMLAdapter(), IrishJobsXMLAdapter(),
                GenericXMLAdapter("test")]

    @pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.board_name)
    def test_feed_is_valid_xml(self, adapter):
        feed = adapter.generate_feed(JOBS)
        ET.fromstring(feed)

    @pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.board_name)
    def test_feed_contains_job_titles(self, adapter):
        feed = adapter.generate_feed(JOBS)
        root = ET.fromstring(feed)
        jobs = root.findall("job")
        assert len(jobs) == 3
        titles = {j.find("title").text for j in jobs}
        assert "Senior Software Engineer" in titles
        assert "CFO" in titles

    @pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.board_name)
    def test_feed_contains_salary_info(self, adapter):
        feed = adapter.generate_feed(JOBS[:1])
        root = ET.fromstring(feed)
        job = root.find("job")
        salary = job.find("salary")
        assert salary is not None
        assert salary.find("min").text == "80000"

    @pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.board_name)
    def test_submit_returns_external_id(self, adapter):
        result = adapter.submit(JOBS[0])
        assert result.external_id is not None
        assert len(result.external_id) > 0

    @pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.board_name)
    def test_submit_and_remove_cycle(self, adapter):
        submit_result = adapter.submit(JOBS[0])
        assert submit_result.success is True
        remove_result = adapter.remove(submit_result.external_id)
        assert remove_result.success is True
