"""One model call of the punctuation layer: tracked and costed, and made again when the provider hiccups."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run


class AskFailed(RuntimeError):
    """A model call of the punctuation layer keeps failing: the stage stops."""


Runner = Callable[[object, str], Awaitable[tuple[dict, object]]]


async def ask(db: Session, case_id: int, label: str, payload: dict, role: str, runner: Runner) -> dict:
    cfg = ai_config.semantic_punctuation
    for attempt in range(cfg.call_retries + 1):
        try:
            with track_run(db, case_id, label, input_summary=label) as run:
                data, res = await runner(get_generation_provider(), json.dumps(payload, ensure_ascii=False))
                stamp_run(run, res, role)
            return data if isinstance(data, dict) else {}
        except GenerationError as e:
            if attempt == cfg.call_retries:
                raise AskFailed(f"{label} failed: {type(e).__name__}: {e}"[:300]) from e
            await asyncio.sleep(cfg.retry_pause_s)
    return {}
