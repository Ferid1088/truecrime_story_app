"""SearXNG JSON API adapter — primary general web discovery backend."""
from __future__ import annotations

import logging
import os

import httpx

from app.research_engine.search import SearchBackend, SearchError, SearchResult
from app.research_engine.urlnorm import canonicalize_url, domain_of

log = logging.getLogger(__name__)

_LANG_TO_SEARXNG = {
    "en": "en-US", "de": "de-DE", "fa": "fa", "ar": "ar-SA",
}


class SearXNGSearchBackend:
    """Talks to a self-hosted SearXNG instance's /search JSON API."""

    name = "searxng"

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float = 25.0,
        max_response_bytes: int = 2_000_000,
    ):
        self.base_url = (base_url or os.getenv("TRUECRIME_SEARXNG_URL") or "").rstrip("/")
        self.timeout_s = timeout_s
        self.max_response_bytes = max_response_bytes

    def is_available(self) -> bool:
        return bool(self.base_url)

    async def health(self) -> dict:
        if not self.base_url:
            return {"backend": self.name, "healthy": False, "reason": "no_url"}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self.base_url}/search",
                                params={"q": "test", "format": "json"})
            ok = r.status_code == 200
            return {"backend": self.name, "healthy": ok,
                    "status_code": r.status_code,
                    "base_url": self.base_url}
        except httpx.HTTPError as e:
            return {"backend": self.name, "healthy": False, "reason": str(e)}

    async def search(
        self,
        query: str,
        language: str,
        categories: list[str] | None = None,
        time_range: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        if not self.base_url:
            raise SearchError(self.name, "not_configured", "SearXNG URL not set")
        params: dict[str, str] = {
            "q": query,
            "format": "json",
            "language": _LANG_TO_SEARXNG.get(language, language or "en-US"),
            "safesearch": "0",
        }
        if categories:
            params["categories"] = ",".join(categories)
        if time_range:
            params["time_range"] = time_range
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={"Accept": "application/json"},
            ) as c:
                r = await c.get(f"{self.base_url}/search", params=params)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as e:
            raise SearchError(self.name, "http_error",
                              f"HTTP {e.response.status_code}") from e
        except (httpx.HTTPError, ValueError) as e:
            raise SearchError(self.name, "request_failed", str(e)) from e

        out: list[SearchResult] = []
        for i, raw in enumerate(data.get("results") or []):
            url = (raw.get("url") or "").strip()
            if not url:
                continue
            out.append(SearchResult(
                backend=self.name,
                query=query,
                language=language,
                rank=i + 1,
                url=url,
                canonical_url=canonicalize_url(url),
                title=(raw.get("title") or "").strip() or None,
                snippet=(raw.get("content") or "").strip() or None,
                domain=domain_of(url),
                published_at=raw.get("publishedDate"),
                scores={
                    "engine_score": raw.get("score"),
                    "engines": raw.get("engines") or [],
                },
            ))
            if len(out) >= limit:
                break
        return out
