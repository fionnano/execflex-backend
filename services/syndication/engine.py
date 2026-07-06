"""
Syndication engine — orchestrates job posting to multiple boards.
"""
from typing import Dict, List, Optional

from .adapters import (
    BoardAdapter,
    GenericXMLAdapter,
    GoogleIndexingStubAdapter,
    IndeedXMLAdapter,
    IrishJobsXMLAdapter,
    JobPosting,
    LinkedInXMLAdapter,
    SyndicationResult,
)

BUILT_IN_ADAPTERS: Dict[str, BoardAdapter] = {
    "linkedin": LinkedInXMLAdapter(),
    "indeed": IndeedXMLAdapter(),
    "irishjobs": IrishJobsXMLAdapter(),
    "google_indexing": GoogleIndexingStubAdapter(),
}


class SyndicationEngine:
    def __init__(self, adapters: Optional[Dict[str, BoardAdapter]] = None):
        self._adapters = adapters or BUILT_IN_ADAPTERS.copy()

    @property
    def available_boards(self) -> List[str]:
        return list(self._adapters.keys())

    def register_adapter(self, adapter: BoardAdapter) -> None:
        self._adapters[adapter.board_name] = adapter

    def syndicate(self, job: JobPosting, boards: List[str]) -> List[SyndicationResult]:
        results = []
        for board in boards:
            adapter = self._adapters.get(board)
            if not adapter:
                adapter = GenericXMLAdapter(board_name=board)
            try:
                result = adapter.submit(job)
            except Exception as e:
                result = SyndicationResult(
                    board=board,
                    success=False,
                    error=f"{type(e).__name__}: {e}",
                )
            results.append(result)
        return results

    def remove(self, board: str, external_id: str) -> SyndicationResult:
        adapter = self._adapters.get(board)
        if not adapter:
            return SyndicationResult(board=board, success=False, error="Unknown board")
        return adapter.remove(external_id)

    def generate_feed(self, board: str, jobs: List[JobPosting]) -> Optional[str]:
        adapter = self._adapters.get(board)
        if not adapter:
            return None
        return adapter.generate_feed(jobs)
