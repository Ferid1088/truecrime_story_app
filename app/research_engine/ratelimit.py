"""Per-domain rate limiting (Part 12) — never hammer websites."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class DomainRateLimiter:
    """Enforces a minimum delay between requests to the same domain
    and caps total concurrency. Honors server Retry-After hints."""

    def __init__(self, min_delay_s: float = 1.0, max_concurrency: int = 4):
        self.min_delay_s = min_delay_s
        self._last: dict[str, float] = defaultdict(float)
        self._wait_until: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sem = asyncio.Semaphore(max_concurrency)

    async def acquire(self, domain: str):
        await self._sem.acquire()
        lock = self._locks[domain]
        await lock.acquire()
        try:
            now = time.monotonic()
            wait = max(
                self._wait_until.get(domain, 0.0),
                self._last.get(domain, 0.0) + self.min_delay_s,
            ) - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[domain] = time.monotonic()
        finally:
            lock.release()

    def release(self):
        self._sem.release()

    def honor_retry_after(self, domain: str, retry_after_s: float | None):
        if retry_after_s and retry_after_s > 0:
            self._wait_until[domain] = max(
                self._wait_until.get(domain, 0.0),
                time.monotonic() + min(retry_after_s, 120.0),
            )
