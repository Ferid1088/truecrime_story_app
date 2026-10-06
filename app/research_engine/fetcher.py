"""Document fetcher (Parts 9-12).

Order: validate URL -> cache lookup -> conditional GET (ETag/
Last-Modified) -> validated redirect chain -> bounded body read.

Every hop is re-validated against the SSRF guard. Per-domain rate
limits and Retry-After are honored. Results are classified into a
FetchOutcome — the engine never fabricates missing content.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.research_engine.fetch_cache import CachedDocument, FetchCache, content_hash
from app.research_engine.ratelimit import DomainRateLimiter
from app.research_engine.safety import UnsafeURLError, validate_url
from app.research_engine.urlnorm import canonicalize_url, domain_of

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
       "TrueCrimeResearch/1.0")

_FETCHABLE_TYPES = (
    "text/html", "application/xhtml", "text/plain",
    "application/pdf", "application/octet-stream",
)


@dataclass
class FetchOutcome:
    """Result of attempting to retrieve one URL — honest about failure."""
    original_url: str
    canonical_url: str | None
    final_url: str | None = None
    status: str = "failed"          # ok | not_modified | failed | blocked
    status_code: int | None = None
    content_type: str | None = None
    body: bytes | None = None
    content_hash: str | None = None
    cache_hit: bool = False
    error: str | None = None
    redirect_hops: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class DocumentFetcher:
    def __init__(
        self,
        cache: FetchCache | None = None,
        limiter: DomainRateLimiter | None = None,
        timeout_s: float = 30.0,
        max_bytes: int = 8_000_000,
        max_redirects: int = 5,
        min_delay_s: float = 1.0,
        max_concurrency: int = 4,
        resolve_dns: bool = True,
    ):
        self.cache = cache or FetchCache()
        self.limiter = limiter or DomainRateLimiter(
            min_delay_s=min_delay_s, max_concurrency=max_concurrency)
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.resolve_dns = resolve_dns

    async def fetch(self, url: str) -> FetchOutcome:
        canonical = canonicalize_url(url)
        out = FetchOutcome(original_url=url, canonical_url=canonical)
        if not canonical:
            out.error = "invalid_url"
            return out
        # Cache hit (fresh) — no network.
        cached = self.cache.get(canonical)
        if cached is not None:
            out.status = "ok"
            out.final_url = cached.final_url
            out.status_code = cached.status_code
            out.content_type = cached.content_type
            out.body = cached.body
            out.content_hash = cached.content_hash
            out.cache_hit = True
            out.meta["cached_at"] = cached.fetched_at
            return out

        try:
            validate_url(canonical_url_to_https(canonical), self.resolve_dns)
        except UnsafeURLError as e:
            out.status = "blocked"
            out.error = e.reason
            return out

        stale = self.cache.get(canonical, allow_stale=True)
        domain = domain_of(canonical)
        await self.limiter.acquire(domain)
        try:
            outcome = await self._request_chain(
                canonical, stale, out)
        finally:
            self.limiter.release()
        return outcome

    async def _request_chain(self, canonical: str,
                             stale: CachedDocument | None,
                             out: FetchOutcome) -> FetchOutcome:
        headers = {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/pdf,"
                      "text/plain;q=0.8,*/*;q=0.5",
            "Accept-Language": "en,de,fa,ar;q=0.7,*;q=0.4",
        }
        if stale is not None:
            if stale.etag:
                headers["If-None-Match"] = stale.etag
            if stale.last_modified:
                headers["If-Modified-Since"] = stale.last_modified

        current = f"https://{canonical}"
        domain = domain_of(current)
        async with httpx.AsyncClient(
            timeout=self.timeout_s,
            follow_redirects=False,
            max_redirects=0,
        ) as client:
            for hop in range(self.max_redirects + 1):
                try:
                    r = await client.get(current, headers=headers)
                except httpx.HTTPError as e:
                    out.error = f"request_failed:{type(e).__name__}"
                    return out
                self.limiter.honor_retry_after(
                    domain, _retry_after(r.headers.get("retry-after")))
                out.status_code = r.status_code

                if r.status_code == 304 and stale is not None:
                    out.status = "not_modified"
                    out.final_url = stale.final_url
                    out.content_type = stale.content_type
                    out.body = stale.body
                    out.content_hash = stale.content_hash
                    out.cache_hit = True
                    return out
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        out.error = "redirect_no_location"
                        return out
                    nxt = httpx.URL(current).join(loc)
                    try:
                        validate_url(str(nxt), self.resolve_dns)
                    except UnsafeURLError as e:
                        out.status = "blocked"
                        out.error = f"unsafe_redirect:{e.reason}"
                        return out
                    out.redirect_hops += 1
                    current = str(nxt)
                    domain = domain_of(current)
                    await self.limiter.acquire(domain)
                    try:
                        continue
                    finally:
                        self.limiter.release()
                if r.status_code == 429 or r.status_code >= 500:
                    out.error = f"http_{r.status_code}"
                    return out
                if r.status_code in (401, 403):
                    out.status = "blocked"
                    out.error = f"http_{r.status_code}"
                    return out
                if r.status_code != 200:
                    out.error = f"http_{r.status_code}"
                    return out

                ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                body = r.content[: self.max_bytes]
                if not _fetchable(ctype, current):
                    out.status = "failed"
                    out.error = f"unfetchable_type:{ctype or 'unknown'}"
                    return out
                chash = content_hash(body)
                doc = CachedDocument(
                    canonical_url=canonicalize_url(current) or canonical,
                    final_url=current,
                    status_code=r.status_code,
                    headers=dict(r.headers),
                    body=body,
                    etag=r.headers.get("etag"),
                    last_modified=r.headers.get("last-modified"),
                    content_hash=chash,
                    fetched_at=time.time(),
                )
                self.cache.put(doc)
                out.status = "ok"
                out.final_url = current
                out.content_type = ctype
                out.body = body
                out.content_hash = chash
                return out
        out.error = "too_many_redirects"
        return out


def _fetchable(ctype: str, url: str) -> bool:
    if any(ctype.startswith(t) or t in ctype for t in _FETCHABLE_TYPES):
        return True
    return url.lower().split("?")[0].endswith(".pdf")


def _retry_after(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def canonical_url_to_https(canonical: str) -> str:
    return canonical if canonical.startswith("http") else f"https://{canonical}"
