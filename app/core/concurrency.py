"""Shared limits for work that runs at the same time.

The documentary engine runs in parallel on two levels:

1. inside one documentary — the language branches (spoken text, voice
   performance, voice, production, critics, render) run side by side,
   image checks and critics are gathered, voice blocks are synthesized
   together;
2. several documentaries at once — jobs wait in "queued" until a slot
   is free (config: concurrency.jobs).

Every external resource has its own named limit (config: concurrency),
shared by everything in the process: LLM calls, vision calls, ElevenLabs
speech (the plan allows 5 concurrent requests), sound generation, local
speech-to-text and video renders. A limit is an asyncio semaphore per
event loop, so tests with their own loops never share state.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Iterable
from typing import Any, TypeVar
from weakref import WeakKeyDictionary

from app.core.ai_config import ai_config

T = TypeVar("T")

_LIMITS: "WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]]" = (
    WeakKeyDictionary()
)
_ACTIVE: "WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, int]]" = WeakKeyDictionary()


def limit_size(name: str) -> int:
    return max(1, int(getattr(ai_config.concurrency, name, 1)))


def limiter(name: str) -> asyncio.Semaphore:
    """The process-wide semaphore called `name` for the running loop."""
    loop = asyncio.get_running_loop()
    per_loop = _LIMITS.setdefault(loop, {})
    sem = per_loop.get(name)
    if sem is None:
        sem = per_loop[name] = asyncio.Semaphore(limit_size(name))
    return sem


@contextlib.asynccontextmanager
async def slot(name: str):
    """`async with slot("llm"):` — waits for a free slot of that limit."""
    sem = limiter(name)
    loop = asyncio.get_running_loop()
    active = _ACTIVE.setdefault(loop, {})
    async with sem:
        active[name] = active.get(name, 0) + 1
        try:
            yield
        finally:
            active[name] -= 1


def usage() -> dict[str, dict[str, int]]:
    """Current use of every limit (for the API / UI)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {}
    active = _ACTIVE.get(loop, {})
    names = set(ai_config.concurrency.model_dump()) | set(active)
    return {n: {"active": active.get(n, 0), "limit": limit_size(n)} for n in sorted(names)}


async def gather_limited(name: str | None, coros: Iterable[Awaitable[T]],
                         return_exceptions: bool = False) -> list[Any]:
    """asyncio.gather where each coroutine first takes a slot of `name`
    (None = no extra limit: the coroutines limit themselves). Results keep
    the input order."""

    async def run(c: Awaitable[T]):
        if name is None:
            return await c
        async with slot(name):
            return await c

    results = await asyncio.gather(*(run(c) for c in coros),
                                   return_exceptions=return_exceptions)
    if return_exceptions:
        # Only ordinary errors are returned as results; cancellation and
        # other control-flow signals (BaseException) still propagate —
        # after every branch has finished.
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, Exception):
                raise r
    return results
