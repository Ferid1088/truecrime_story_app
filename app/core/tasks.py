"""Background tasks that must not be garbage-collected.

The event loop keeps only weak references to tasks; a fire-and-forget
`create_task(...)` can vanish mid-run. `spawn` keeps a strong reference
until the task is done and logs an unexpected failure."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()


def spawn(coro: Awaitable, name: str | None = None) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("background task %s failed: %r", t.get_name(), t.exception())

    task.add_done_callback(_done)
    return task


def running() -> int:
    return len(_tasks)
