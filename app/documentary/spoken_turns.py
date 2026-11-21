"""Who writes its spoken text now: at most `concurrency.spoken_text` languages at the same time, the first ones in the order of `spoken.text_order`.

A language that is ready earlier than a language before it in the order still waits for that language, but only while that language runs a film of the
same case and has not started its spoken text (a language that is not selected, failed or finished never holds the others back), and never longer than
`spoken.text_order_wait_s`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from app.core.ai_config import ai_config
from app.core.concurrency import slot
from app.db.base import SessionLocal
from app.db.models import DocumentaryJob

POLL_SECONDS = 2.0
_started: set[tuple[int, str]] = set()      # (case, language) that went into its turn in this process


def rank(language: str) -> int:
    """The place of a language in the order: the configured ones first, then the production languages as they are listed."""
    first = list(ai_config.spoken.text_order)
    rest = [lang for lang in ai_config.documentary.languages if lang not in first]
    order = first + rest
    return order.index(language) if language in order else len(order)


def running_languages(case_id: int) -> set[str]:
    """The languages of the case that have a film queued or running now."""
    with SessionLocal() as db:
        rows = (db.query(DocumentaryJob.languages_json)
                .filter(DocumentaryJob.case_id == case_id, DocumentaryJob.status.in_(("queued", "running"))).all())
    return {lang for (raw,) in rows for lang in json.loads(raw or "[]")}


def _waiting_for(case_id: int, language: str, running) -> list[str]:
    mine = rank(language)
    return [lang for lang in running(case_id)
            if rank(lang) < mine and (case_id, lang) not in _started]


@contextlib.asynccontextmanager
async def spoken_turn(case_id: int, language: str, running=running_languages):
    """`async with spoken_turn(case, language):` around the writing of one language's spoken text."""
    deadline = time.monotonic() + ai_config.spoken.text_order_wait_s
    while _waiting_for(case_id, language, running) and time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECONDS)
    async with slot("spoken_text"):
        _started.add((case_id, language))
        yield
