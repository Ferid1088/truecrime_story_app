"""Search backend interface.

SearchBackends return raw results; they never fetch pages and never call an
LLM.  The registry is intentionally a single-provider boundary: search
failures are fatal to the job and are never hidden by a fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)

MAX_QUERY_WORDS = 8


def shorten_query(query: str, max_words: int = MAX_QUERY_WORDS) -> str:
    """Keep provider queries compact and predictable.

    The planner is allowed to be verbose, but the paid search boundary is
    deliberately bounded so each provider call remains discovery-oriented.
    """
    words = (query or "").split()
    return " ".join(words[:max_words])


@dataclass
class SearchResult:
    """One raw result from a search backend (pre-fetch, pre-acceptance)."""

    backend: str
    query: str
    language: str          # query language
    rank: int
    url: str               # original URL as returned
    canonical_url: str | None = None
    title: str | None = None
    snippet: str | None = None
    domain: str | None = None
    detected_language: str | None = None
    published_at: str | None = None
    scores: dict = field(default_factory=dict)


class SearchError(RuntimeError):
    def __init__(self, backend: str, kind: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.backend = backend
        self.kind = kind
        self.details = details or {}


class SearchBackend(Protocol):
    name: str

    def is_available(self) -> bool: ...

    async def search(
        self,
        query: str,
        language: str,
        categories: list[str] | None = None,
        time_range: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]: ...


class SearchBackendRegistry:
    """One configured backend; retained as the existing search interface."""

    def __init__(self, backends: list[SearchBackend]):
        self.backends = list(backends)

    def available(self) -> list[SearchBackend]:
        return [b for b in self.backends if b.is_available()]

    async def search(self, query: str, language: str, limit: int = 10,
                     categories: list[str] | None = None,
                     time_range: str | None = None) -> list[SearchResult]:
        if not self.backends:
            raise SearchError("registry", "search_unavailable",
                              "search unavailable: no search provider configured")
        backend = self.backends[0]
        if not backend.is_available():
            raise SearchError(backend.name, "not_configured",
                              f"{backend.name.title()} Search API is not configured")
        results = await backend.search(
            query, language, categories=categories, time_range=time_range, limit=limit)
        if not results:
            detail = getattr(backend, "last_search_diagnostics", None) or {}
            raise SearchError(backend.name, "search_unavailable",
                              "search unavailable: provider returned 0 results",
                              detail)
        return results
