"""Brave Search Web API adapter.

This is the only web-search adapter.  The API key is read from the process
environment and is never included in diagnostics or exceptions.
"""
from __future__ import annotations

import asyncio
import email.utils
import logging
import time
from datetime import UTC, datetime

import httpx

from app.core.ai_config import ai_config
from app.core.env import env_value
from app.core.observability import health_probe_var
from app.research_engine.search import SearchError, SearchResult, shorten_query
from app.research_engine.urlnorm import canonicalize_url, domain_of

_HEALTH_QUERY = "recent true crime investigation case"
_health_cache: dict[str, tuple[float, dict]] = {}
log = logging.getLogger(__name__)

class BraveSearchBackend:
    name = "brave"

    def __init__(self, api_key: str | None = None,
                 base_url: str = "https://api.search.brave.com",
                 timeout_s: float = 10.0, health_cache_seconds: float = 600.0,
                 result_count: int = 10, min_interval_s: float = 1.0):
        self.api_key = api_key if api_key is not None else env_value("BRAVE_SEARCH_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.health_cache_seconds = health_cache_seconds
        self.result_count = min(max(int(result_count), 1), 20)
        self.min_interval_s = max(float(min_interval_s), 0.0)
        self._rate_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self.last_search_diagnostics: dict | None = None

    def is_available(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                return max((when - datetime.now(UTC)).total_seconds(), 0.0)
            except (TypeError, ValueError, OverflowError):
                return None

    async def _request(self, query: str, language: str, limit: int,
                       endpoint: str = "web") -> tuple[dict, int]:
        if not self.api_key:
            raise SearchError(self.name, "not_configured",
                              "Brave Search API is not configured")
        query = shorten_query(query)
        params: dict[str, str | int] = {
            "q": query,
            "count": min(max(limit, 1), self.result_count, 20),
            "safesearch": "strict",
        }
        # Brave answers HTTP 422 to any search_lang outside its own enum
        # (checked live 2026-10-16: fa and a bare zh -> 422; zh-hans, ja, fr,
        # es, it, ar, de -> 200; checked live 2026-10-23: a code outside the
        # enum, e.g. the UI's "other" bucket, also -> 422), so the code comes
        # from the vetted language catalog only — never guessed from the
        # first two letters of whatever language string we were given.
        search_lang = ai_config.multilingual.brave_search_lang(language)
        if search_lang:
            params["search_lang"] = search_lang
        headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
        last_status: int | None = None
        attempts: list[dict] = []
        for attempt in range(2):
            try:
                async with self._rate_lock:
                    wait = self._next_request_at - time.monotonic()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    async with httpx.AsyncClient(timeout=self.timeout_s, headers=headers) as client:
                        response = await client.get(f"{self.base_url}/res/v1/{endpoint}/search", params=params)
                    retry_after = self._retry_after(response.headers.get("Retry-After"))
                    self._next_request_at = time.monotonic() + self.min_interval_s
                    if retry_after is not None:
                        self._next_request_at = max(self._next_request_at,
                                                    time.monotonic() + retry_after)
                last_status = response.status_code
                try:
                    response_data = response.json()
                    count = len(self._results(response_data)) if isinstance(response_data, dict) else 0
                except (ValueError, TypeError):
                    response_data, count = None, 0
                attempts.append({
                    "attempt": attempt + 1,
                    "http_status": response.status_code,
                    "result_count": count,
                    "retry_after_s": retry_after,
                })
                log.info("Brave search status=%s results=%s retry=%s retry_after_s=%s query=%r",
                         response.status_code, count, attempt, retry_after, query)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        continue
                    raise SearchError(self.name, "search_unavailable",
                                      f"search unavailable: Brave returned HTTP {response.status_code} "
                                      f"after retry (results={count})",
                                      {"http_status": response.status_code, "attempts": attempts,
                                       "retries": attempt, "rate_limited": response.status_code == 429})
                if response.status_code >= 400:
                    raise SearchError(self.name, "search_unavailable",
                                      f"search unavailable: Brave returned HTTP {response.status_code} "
                                      f"(results={count})",
                                      {"http_status": response.status_code, "attempts": attempts,
                                       "retries": attempt})
                data = response_data if response_data is not None else response.json()
                if not isinstance(data, dict):
                    raise ValueError("invalid Brave response")
                self.last_search_diagnostics = {
                    "query": query, "http_status": response.status_code,
                    "result_count": count, "retries": attempt,
                    "attempts": attempts, "provider": self.name,
                    "rate_limited": any(a["http_status"] == 429 for a in attempts),
                }
                return data, response.status_code
            except SearchError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise SearchError(self.name, "search_unavailable",
                                  "search unavailable: Brave Search request failed") from exc
        raise SearchError(self.name, "search_unavailable",
                          f"search unavailable: Brave Search request failed ({last_status or 'unknown'})",
                          {"http_status": last_status, "attempts": attempts,
                           "retries": len(attempts), "rate_limited": any(
                               a["http_status"] == 429 for a in attempts)})

    @staticmethod
    def _results(data: dict) -> list[dict]:
        top = data.get("results")  # the images and videos endpoints answer with a flat list
        if isinstance(top, list):
            return top
        web = data.get("web")
        return (web.get("results") or []) if isinstance(web, dict) else []

    async def search_media(self, kind: str, query: str, language: str,
                           limit: int = 10) -> list[dict]:
        """Brave image or video search results, as Brave returns them (checked
        live 2026-10-16: images carry properties.url, the original image, and
        the page url, no license; videos carry url, title, video.creator and
        video.duration). An empty answer is an empty list, not an error."""
        if kind not in ("images", "videos"):
            raise ValueError(f"unknown Brave media kind {kind!r}")
        data, _ = await self._request(shorten_query(query), language, limit, endpoint=kind)
        return self._results(data)[:min(limit, self.result_count, 20)]

    async def search(self, query: str, language: str,
                     categories: list[str] | None = None,
                     time_range: str | None = None,
                     limit: int = 10) -> list[SearchResult]:
        query = shorten_query(query)
        data, status = await self._request(query, language, limit)
        raw_results = self._results(data)
        self.last_search_diagnostics = {
            "query": query,
            "http_status": status,
            "result_count": len(raw_results),
            "retries": (self.last_search_diagnostics or {}).get("retries", 0),
            "attempts": (self.last_search_diagnostics or {}).get("attempts", []),
            "rate_limited": (self.last_search_diagnostics or {}).get("rate_limited", False),
            "estimated_cost_usd": 0.005,
            "provider": self.name,
        }
        if not raw_results:
            raise SearchError(self.name, "search_unavailable",
                              f"search unavailable: Brave returned 0 results (HTTP {status}; "
                              f"retries={self.last_search_diagnostics.get('retries', 0)})",
                              self.last_search_diagnostics)
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
                              f"search unavailable: Brave returned 0 usable results (HTTP {status})",
                              self.last_search_diagnostics)
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
