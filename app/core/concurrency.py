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
import time
from collections.abc import Awaitable, Iterable
from typing import Any, TypeVar
from weakref import WeakKeyDictionary

from app.core.ai_config import ai_config

T = TypeVar("T")

# Per provider limit: how far a rate-limit answer (HTTP 429, an overloaded gateway) has pulled it down, process-wide. The limit
# shrinks at once and grows back one slot per `recovery_seconds` once `cooldown_seconds` have passed without another report, so a
# provider that says "too many" is given room and is probed again, never hammered.
_THROTTLE: dict[str, dict[str, float]] = {}
_GATES: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, Gate]] = WeakKeyDictionary()
_ACTIVE: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, int]] = WeakKeyDictionary()


def limit_size(name: str) -> int:
    return max(1, int(getattr(ai_config.concurrency, name, 1)))


def _policy(name: str):
    cfg = ai_config.orchestrator_settings()
    return cfg.throttle_overrides.get(name, cfg.throttle)


def effective_limit(name: str, now: float | None = None) -> int:
    """How many calls of `name` may run now: the configured limit, pulled down while the provider is throttling."""
    configured = limit_size(name)
    state = _THROTTLE.get(name)
    if state is None:
        return configured
    policy = _policy(name)
    now = time.monotonic() if now is None else now
    quiet = now - state["at"] - policy.cooldown_seconds
    grown = int(quiet // policy.recovery_seconds) + 1 if quiet >= 0 else 0
    return max(1, min(configured, int(state["to"]) + grown))


def report_throttle(name: str, now: float | None = None) -> int:
    """A provider answered "too many requests" (or an overloaded gateway): the limit of `name` shrinks. Returns the new limit."""
    policy = _policy(name)
    now = time.monotonic() if now is None else now
    current = effective_limit(name, now)
    shrunk = max(policy.min_limit, int(current * policy.shrink_factor))
    _THROTTLE[name] = {"to": float(shrunk), "at": now}
    return shrunk


def throttled(name: str, now: float | None = None) -> bool:
    return effective_limit(name, now) < limit_size(name)


def reset_throttles() -> None:
    _THROTTLE.clear()


class Gate:
    """The process-wide limit called `name` for one event loop: a semaphore whose size follows `effective_limit`."""

    POLL_SECONDS = 1.0

    def __init__(self, name: str):
        self.name = name
        self.active = 0
        self._changed = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._changed:
            while self.active >= effective_limit(self.name):
                try:
                    await asyncio.wait_for(self._changed.wait(), self.POLL_SECONDS)   # re-read the limit: it recovers with time
                except TimeoutError:
                    continue
            self.active += 1

    async def release(self) -> None:
        async with self._changed:
            self.active -= 1
            self._changed.notify_all()


def limiter(name: str) -> Gate:
    """The process-wide gate called `name` for the running loop."""
    loop = asyncio.get_running_loop()
    per_loop = _GATES.setdefault(loop, {})
    gate = per_loop.get(name)
    if gate is None:
        gate = per_loop[name] = Gate(name)
    return gate


@contextlib.asynccontextmanager
async def slot(name: str):
    """`async with slot("llm"):` — waits for a free slot of that limit."""
    gate = limiter(name)
    loop = asyncio.get_running_loop()
    active = _ACTIVE.setdefault(loop, {})
    await gate.acquire()
    active[name] = active.get(name, 0) + 1
    try:
        yield
    finally:
        active[name] -= 1
        await gate.release()


def usage() -> dict[str, dict[str, int | bool]]:
    """Current use of every limit (for the API / UI): calls running, the limit now and the configured one."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {}
    active = _ACTIVE.get(loop, {})
    names = set(ai_config.concurrency.model_dump()) | set(active)
    return {n: {"active": active.get(n, 0), "limit": effective_limit(n), "configured": limit_size(n),
                "throttled": throttled(n)} for n in sorted(names)}


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
