"""
Job board syndication adapters.

Architecture pattern ported from Ainm's job syndication (Google Indexing API,
XML feeds, per-board adapters). All adapters implement BoardAdapter protocol.
All run against mock/stub endpoints — zero live API calls.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol
from xml.etree.ElementTree import Element, SubElement, tostring


@dataclass
class JobPosting:
    id: str
    title: str
    description: str
    location: str
    company_name: str
    pay_range_min: float = 0
    pay_range_max: float = 0
    pay_currency: str = "EUR"
    pay_period: str = "annual"
    commitment_type: str = ""
    industry: str = ""
    is_remote: bool = False
    apply_url: str = ""
    posted_at: str = ""
    expires_at: str = ""


@dataclass
class SyndicationResult:
    board: str
    success: bool
    external_id: Optional[str] = None
    error: Optional[str] = None
    feed_xml: Optional[str] = None


class BoardAdapter(Protocol):
    board_name: str

    def submit(self, job: JobPosting) -> SyndicationResult: ...

    def remove(self, external_id: str) -> SyndicationResult: ...

    def generate_feed(self, jobs: List[JobPosting]) -> str: ...


def _base_xml_job(job: JobPosting) -> Element:
    """Build common XML job element used by LinkedIn/Indeed/generic feeds."""
    item = Element("job")
    SubElement(item, "title").text = job.title
    SubElement(item, "description").text = f"<![CDATA[{job.description}]]>"
    SubElement(item, "location").text = job.location
    SubElement(item, "company").text = job.company_name
    SubElement(item, "id").text = job.id
    SubElement(item, "url").text = job.apply_url or f"https://execflex.ai/apply/{job.id}"
    SubElement(item, "type").text = job.commitment_type
    SubElement(item, "industry").text = job.industry

    salary = SubElement(item, "salary")
    SubElement(salary, "min").text = str(job.pay_range_min)
    SubElement(salary, "max").text = str(job.pay_range_max)
    SubElement(salary, "currency").text = job.pay_currency
    SubElement(salary, "period").text = job.pay_period

    if job.posted_at:
        SubElement(item, "date").text = job.posted_at
    if job.expires_at:
        SubElement(item, "expiry").text = job.expires_at
    if job.is_remote:
        SubElement(item, "remote").text = "true"

    return item


class LinkedInXMLAdapter:
    board_name = "linkedin"

    def submit(self, job: JobPosting) -> SyndicationResult:
        feed = self.generate_feed([job])
        return SyndicationResult(
            board=self.board_name,
            success=True,
            external_id=f"li-{job.id[:8]}",
            feed_xml=feed,
        )

    def remove(self, external_id: str) -> SyndicationResult:
        return SyndicationResult(board=self.board_name, success=True, external_id=external_id)

    def generate_feed(self, jobs: List[JobPosting]) -> str:
        root = Element("source")
        SubElement(root, "publisher").text = "ExecFlex"
        SubElement(root, "publisherurl").text = "https://execflex.ai"
        for job in jobs:
            item = _base_xml_job(job)
            item.tag = "job"
            loc = item.find("location")
            if loc is not None:
                loc.tag = "location"
                SubElement(item, "city").text = job.location.split(",")[0].strip() if "," in job.location else job.location
                SubElement(item, "country").text = job.location.split(",")[-1].strip() if "," in job.location else "IE"
            root.append(item)
        return tostring(root, encoding="unicode", xml_declaration=True)


class IndeedXMLAdapter:
    board_name = "indeed"

    def submit(self, job: JobPosting) -> SyndicationResult:
        feed = self.generate_feed([job])
        return SyndicationResult(
            board=self.board_name,
            success=True,
            external_id=f"ind-{job.id[:8]}",
            feed_xml=feed,
        )

    def remove(self, external_id: str) -> SyndicationResult:
        return SyndicationResult(board=self.board_name, success=True, external_id=external_id)

    def generate_feed(self, jobs: List[JobPosting]) -> str:
        root = Element("source")
        SubElement(root, "publisher").text = "ExecFlex"
        SubElement(root, "publisherurl").text = "https://execflex.ai"
        SubElement(root, "lastBuildDate").text = ""
        for job in jobs:
            item = _base_xml_job(job)
            item.tag = "job"
            SubElement(item, "referencenumber").text = job.id
            SubElement(item, "sourcename").text = "ExecFlex"
            root.append(item)
        return tostring(root, encoding="unicode", xml_declaration=True)


class IrishJobsXMLAdapter:
    """Generic XML adapter targeting IrishJobs.ie feed spec.

    IrishJobs does not publish a public feed spec, so this uses a generic
    XML structure compatible with common Irish job board feed formats.
    Decision logged in DECISIONS.md as D-15.
    """
    board_name = "irishjobs"

    def submit(self, job: JobPosting) -> SyndicationResult:
        feed = self.generate_feed([job])
        return SyndicationResult(
            board=self.board_name,
            success=True,
            external_id=f"ij-{job.id[:8]}",
            feed_xml=feed,
        )

    def remove(self, external_id: str) -> SyndicationResult:
        return SyndicationResult(board=self.board_name, success=True, external_id=external_id)

    def generate_feed(self, jobs: List[JobPosting]) -> str:
        root = Element("jobs")
        root.set("source", "ExecFlex")
        for job in jobs:
            item = _base_xml_job(job)
            SubElement(item, "reference").text = job.id
            SubElement(item, "region").text = "Ireland"
            root.append(item)
        return tostring(root, encoding="unicode", xml_declaration=True)


class GenericXMLAdapter:
    """Fallback adapter for boards without a specific spec."""

    def __init__(self, board_name: str = "generic"):
        self.board_name = board_name

    def submit(self, job: JobPosting) -> SyndicationResult:
        feed = self.generate_feed([job])
        return SyndicationResult(
            board=self.board_name,
            success=True,
            external_id=f"gen-{job.id[:8]}",
            feed_xml=feed,
        )

    def remove(self, external_id: str) -> SyndicationResult:
        return SyndicationResult(board=self.board_name, success=True, external_id=external_id)

    def generate_feed(self, jobs: List[JobPosting]) -> str:
        root = Element("feed")
        root.set("source", "ExecFlex")
        root.set("board", self.board_name)
        for job in jobs:
            root.append(_base_xml_job(job))
        return tostring(root, encoding="unicode", xml_declaration=True)


class GoogleIndexingStubAdapter:
    """Stub for Google Indexing API — logs intent but makes no real API calls."""
    board_name = "google_indexing"

    def submit(self, job: JobPosting) -> SyndicationResult:
        url = job.apply_url or f"https://execflex.ai/jobs/{job.id}"
        return SyndicationResult(
            board=self.board_name,
            success=True,
            external_id=f"gidx-{job.id[:8]}",
            feed_xml=f'{{"url": "{url}", "type": "URL_UPDATED"}}',
        )

    def remove(self, external_id: str) -> SyndicationResult:
        return SyndicationResult(
            board=self.board_name,
            success=True,
            external_id=external_id,
            feed_xml=f'{{"url": "https://execflex.ai/jobs/{external_id}", "type": "URL_DELETED"}}',
        )

    def generate_feed(self, jobs: List[JobPosting]) -> str:
        import json
        entries = [{"url": j.apply_url or f"https://execflex.ai/jobs/{j.id}",
                     "type": "URL_UPDATED"} for j in jobs]
        return json.dumps({"entries": entries})
