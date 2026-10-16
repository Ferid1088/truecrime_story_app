"""Relevance check of borderline YouTube candidates (title, channel,
description): the one decision about a video that is not a keyword score."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


class VideoRelevanceChecker:
    def __init__(self):
        self.gen = get_generation_provider()

    async def check(self, db: Session, case_id: int, case_title: str,
                    candidates: list[dict]) -> dict[str, tuple[bool, str]]:
        """video_id -> (relevant, short reason). A video the model did not
        answer for counts as relevant: it is processed, never silently dropped."""
        verdicts: dict[str, tuple[bool, str]] = {}
        size = ai_config.youtube_research.relevance.check_batch_size
        for start in range(0, len(candidates), size):
            batch = candidates[start:start + size]
            payload = {"case": case_title, "videos": [
                {"id": c["video_id"], "title": c["title"], "channel": c.get("channel_name"),
                 "description": (c.get("description") or "")[:600]} for c in batch]}
            with track_run(db, case_id, "Video Relevance",
                           input_summary=f"{len(batch)} borderline videos") as run:
                raw, res = await run_agent("research.video_relevance", self.gen,
                                           json.dumps(payload, ensure_ascii=False))
                stamp_run(run, res, "video_relevance_checker")
            for item in (raw or {}).get("verdicts") or []:
                if isinstance(item, dict) and item.get("id"):
                    verdicts[str(item["id"])] = (bool(item.get("relevant", True)),
                                                 str(item.get("reason") or "")[:60])
        return verdicts
