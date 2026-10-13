"""Provider-backed Shorts writer/auditor boundary.

The pipeline depends on this small adapter rather than a concrete provider.
Tests inject ``MockShortsTextAdapter`` and can inspect its per-stage call
counter without making a network request.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.db.models import AgentRun
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationResult
from app.services.tracking import stamp_run, track_run


class ShortsTextAdapter(Protocol):
    call_counts: dict[str, int]

    def write(self, db: Session, case_id: int, payload: dict) -> tuple[dict, GenerationResult | None]: ...
    def audit(self, db: Session, case_id: int, payload: dict) -> tuple[dict, GenerationResult | None]: ...


def _sync(awaitable):
    """Run an agent call from both synchronous queue code and an async worker."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    result: list[Any] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except BaseException as exc:  # pragma: no cover - defensive bridge
            error.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


class ProviderShortsTextAdapter:
    """Uses the existing agent runner and generation-provider boundary."""

    def __init__(self, provider=None):
        self.provider = provider or get_generation_provider()
        self.call_counts = {"shorts.write": 0, "shorts.audit": 0}

    def write(self, db: Session, case_id: int, payload: dict):
        self.call_counts["shorts.write"] += 1

        async def call():
            with track_run(db, case_id, "Shorts Writer", input_summary=payload["language"]):
                raw, result = await run_agent("shorts.write", self.provider,
                                              json.dumps(payload, ensure_ascii=False))
                run = db.query(AgentRun).filter(
                    AgentRun.case_id == case_id, AgentRun.agent_name == "Shorts Writer",
                ).order_by(AgentRun.id.desc()).first()
                if run and result is not None:
                    stamp_run(run, result, "chapter_writer")
                return raw, result

        raw, result = _sync(call())
        return raw if isinstance(raw, dict) else {}, result

    def audit(self, db: Session, case_id: int, payload: dict):
        self.call_counts["shorts.audit"] += 1

        async def call():
            with track_run(db, case_id, "Shorts Auditor", input_summary=payload["language"]):
                raw, result = await run_agent("shorts.audit", self.provider,
                                              json.dumps(payload, ensure_ascii=False))
                run = db.query(AgentRun).filter(
                    AgentRun.case_id == case_id, AgentRun.agent_name == "Shorts Auditor",
                ).order_by(AgentRun.id.desc()).first()
                if run and result is not None:
                    stamp_run(run, result, "chapter_auditor")
                return raw, result

        raw, result = _sync(call())
        return raw if isinstance(raw, dict) else {}, result


class MockShortsTextAdapter:
    """Offline producer used only by tests and disposable E2E fixtures."""

    def __init__(self, *, reject: bool = False):
        self.reject = reject
        self.call_counts = {"shorts.write": 0, "shorts.audit": 0}

    def write(self, db: Session, case_id: int, payload: dict):
        self.call_counts["shorts.write"] += 1
        text = payload["spoken_text"][:240].strip() or payload["hook"]
        return {
            "hook": text[:96], "body": text, "cta": "Watch the full evidence-led story.",
            "captions": [{"start": 0.1, "end": min(payload["target_duration"] - 0.1, 4.0),
                          "text": text[:42]}],
        }, None

    def audit(self, db: Session, case_id: int, payload: dict):
        self.call_counts["shorts.audit"] += 1
        return {"approved": not self.reject, "reasons": ["mock rejection"] if self.reject else []}, None
