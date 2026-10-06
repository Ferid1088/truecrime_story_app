"""Search backend interface + registry (Parts 2-3).

SearchBackends return raw results; they never fetch pages and never
call an LLM. The registry provides fallback across adapters so no
single search engine is a point of failure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)


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
    def __init__(self, backend: str, kind: str, message: str):
        super().__init__(message)
        self.backend = backend
        self.kind = kind


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
    """Ordered backends with per-call fallback (Part 3)."""

    def __init__(self, backends: list[SearchBackend]):
        self.backends = list(backends)

    def available(self) -> list[SearchBackend]:
        return [b for b in self.backends if b.is_available()]

    async def search(self, query: str, language: str, limit: int = 10,
                     categories: list[str] | None = None,
                     time_range: str | None = None) -> list[SearchResult]:
        """Try backends in order; merge results from the first that
        returns anything. All failures are logged, never fatal."""
        errors: list[str] = []
        for backend in self.backends:
            if not backend.is_available():
                continue
            try:
                results = await backend.search(
                    query, language,
                    categories=categories, time_range=time_range,
                    limit=limit,
                )
            except Exception as e:  # noqa: BLE001 - adapter isolation
                errors.append(f"{backend.name}: {e}")
                log.warning("search backend %s failed: %s", backend.name, e)
                continue
            if results:
                return results
        if errors and not self.available():
            raise SearchError("registry", "all_backends_failed",
                              "; ".join(errors))
        return []
