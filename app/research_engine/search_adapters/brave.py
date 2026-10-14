"""Brave Search Web API adapter.

This is the only web-search adapter.  The API key is read from the process
environment and is never included in diagnostics or exceptions.
"""
from __future__ import annotations

import time

import httpx

from app.core.env import env_value
from app.core.observability import health_probe_var
from app.research_engine.search import SearchError, SearchResult
from app.research_engine.urlnorm import canonicalize_url, domain_of

_HEALTH_QUERY = "recent true crime investigation case"
_health_cache: dict[str, tuple[float, dict]] = {}


class BraveSearchBackend:
    name = "brave"

    def __init__(self, api_key: str | None = None,
                 base_url: str = "https://api.search.brave.com",
                 timeout_s: float = 10.0, health_cache_seconds: float = 600.0,
                 result_count: int = 10):
        self.api_key = api_key if api_key is not None else env_value("BRAVE_SEARCH_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = 10.0
        self.health_cache_seconds = health_cache_seconds
        self.result_count = min(max(int(result_count), 1), 20)
        self.last_search_diagnostics: dict | None = None

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def _request(self, query: str, language: str, limit: int) -> tuple[dict, int]:
        if not self.api_key:
            raise SearchError(self.name, "not_configured",
                              "Brave Search API is not configured")
        params = {
            "q": query,
            "count": min(max(limit, 1), self.result_count, 20),
            "search_lang": (language or "en")[:2].lower(),
            "safesearch": "strict",
        }
        headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
        last_status: int | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                    response = await client.get(f"{self.base_url}/res/v1/web/search", params=params)
                last_status = response.status_code
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        continue
                    raise SearchError(self.name, "search_unavailable",
                                      "search unavailable: Brave Search request failed")
                if response.status_code >= 400:
                    raise SearchError(self.name, "search_unavailable",
                                      "search unavailable: Brave Search request failed")
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("invalid Brave response")
                return data, response.status_code
            except SearchError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise SearchError(self.name, "search_unavailable",
                                  "search unavailable: Brave Search request failed") from exc
        raise SearchError(self.name, "search_unavailable",
                          f"search unavailable: Brave Search request failed ({last_status or 'unknown'})")

    @staticmethod
    def _results(data: dict) -> list[dict]:
        web = data.get("web")
        return (web.get("results") or []) if isinstance(web, dict) else []

    async def search(self, query: str, language: str,
                     categories: list[str] | None = None,
                     time_range: str | None = None,
                     limit: int = 10) -> list[SearchResult]:
        data, status = await self._request(query, language, limit)
        raw_results = self._results(data)
        self.last_search_diagnostics = {
            "query": query,
            "http_status": status,
            "result_count": len(raw_results),
            "provider": self.name,
        }
        if not raw_results:
            raise SearchError(self.name, "search_unavailable",
                              "search unavailable: Brave returned 0 results")
        out: list[SearchResult] = []
        for i, raw in enumerate(raw_results):
            url = (raw.get("url") or "").strip()
            if not url:
                continue
            out.append(SearchResult(
                backend=self.name, query=query, language=language, rank=i + 1,
                url=url, canonical_url=canonicalize_url(url),
                title=(raw.get("title") or "").strip() or None,
                snippet=(raw.get("description") or "").strip() or None,
                domain=domain_of(url), detected_language=language,
                published_at=raw.get("published_date"),
                scores={"source": self.name},
            ))
            if len(out) >= min(limit, self.result_count, 20):
                break
        if not out:
            raise SearchError(self.name, "search_unavailable",
                              "search unavailable: Brave returned 0 usable results")
        return out

    async def health(self) -> dict:
        cache_key = f"{self.base_url}:{bool(self.api_key)}"
        cached = _health_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return dict(cached[1])
        result = {
            "backend": self.name, "query": _HEALTH_QUERY,
            "healthy": False, "status": "failing", "result_count": 0,
        }
        if not self.api_key:
            result["reason"] = "BRAVE_SEARCH_API_KEY is not configured"
        else:
            token = health_probe_var.set(True)
            try:
                try:
                    data, status = await self._request(_HEALTH_QUERY, "en", self.result_count)
                    count = len(self._results(data))
                    result.update({"status_code": status, "result_count": count})
                    result["status"] = "ok" if count >= 3 else "degraded"
                    result["healthy"] = count >= 3
                    if count < 3:
                        result["reason"] = "discovery probe returned fewer than 3 results"
                finally:
                    health_probe_var.reset(token)
            except SearchError as exc:
                result["reason"] = "search unavailable"
                result["error_kind"] = exc.kind
        _health_cache[cache_key] = (time.monotonic() + self.health_cache_seconds, result)
        return dict(result)
