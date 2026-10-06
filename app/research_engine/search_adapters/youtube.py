"""YouTube discovery adapter (Part 27).

Two independent paths:
1. YouTube Data API v3 when configured (most reliable metadata).
2. SearXNG site-restricted queries as a fallback (no API key needed).

Returns SearchResult rows whose URLs are canonical youtube watch URLs.
"""
from __future__ import annotations

import logging
import os

import httpx

from app.research_engine.search import SearchError, SearchResult
from app.research_engine.urlnorm import canonicalize_url, youtube_video_id

log = logging.getLogger(__name__)


class YouTubeSearchBackend:
    name = "youtube"

    def __init__(self, api_key: str | None = None,
                 searxng=None, timeout_s: float = 20.0):
        self.api_key = api_key if api_key is not None else os.getenv(
            "TrueCrime_YOUTUBE_API_KEY") or os.getenv("YOUTUBE_API_KEY")
        self.searxng = searxng
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        return bool(self.api_key) or bool(
            self.searxng and self.searxng.is_available())

    async def search(
        self,
        query: str,
        language: str,
        categories: list[str] | None = None,
        time_range: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        if self.api_key:
            results = await self._api_search(query, language, limit)
            if results:
                return results
        if self.searxng and self.searxng.is_available():
            return await self._searxng_search(query, language, limit)
        return []

    async def _api_search(self, query: str, language: str,
                          limit: int) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "snippet",
                        "q": query,
                        "type": "video",
                        "maxResults": min(limit, 25),
                        "relevanceLanguage": language,
                        "key": self.api_key,
                    },
                )
                r.raise_for_status()
                data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("youtube api search failed: %s", e)
            raise SearchError(self.name, "api_failed", str(e)) from e
        out = []
        for i, item in enumerate(data.get("items") or []):
            vid = ((item.get("id") or {}).get("videoId") or "")
            snip = item.get("snippet") or {}
            if not vid:
                continue
            url = f"https://www.youtube.com/watch?v={vid}"
            out.append(SearchResult(
                backend=self.name,
                query=query,
                language=language,
                rank=i + 1,
                url=url,
                canonical_url=canonicalize_url(url),
                title=(snip.get("title") or "").strip() or None,
                snippet=(snip.get("description") or "").strip() or None,
                domain="youtube.com",
                detected_language=language,
                published_at=snip.get("publishedAt"),
                scores={
                    "channel": snip.get("channelTitle"),
                    "video_id": vid,
                    "via": "youtube_data_api",
                },
            ))
        return out

    async def _searxng_search(self, query: str, language: str,
                              limit: int) -> list[SearchResult]:
        """General search restricted to youtube video URLs."""
        raw = await self.searxng.search(
            f"site:youtube.com {query}",
            language, limit=limit * 3)
        out = []
        for res in raw:
            vid = youtube_video_id(res.url)
            if not vid:
                continue
            res.backend = self.name
            res.canonical_url = canonicalize_url(res.url)
            res.scores = {**(res.scores or {}), "video_id": vid,
                          "via": "searxng"}
            res.rank = len(out) + 1
            out.append(res)
            if len(out) >= limit:
                break
        return out
