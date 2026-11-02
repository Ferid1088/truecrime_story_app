"""The reading stage of a film's job: writes and checks the reading text of every block before the voice, and stops the run when words are in
doubt (the owner answers them all in one review, then resumes)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, StoryVersion
from app.documentary import voice_prepare as VP
from app.documentary.performance import performance_for_version
from app.documentary.pipeline.errors import AwaitingReadingReview
from app.documentary.reading.block import block_inputs
from app.documentary.reading.text import ReadingStage


def required(language: str) -> bool:
    return language in ai_config.reading.languages


async def run(db: Session, case: Case, version: StoryVersion, language: str) -> dict:
    plan = performance_for_version(db, version)
    items = block_inputs(plan["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
    result = await ReadingStage(db, case.id, version, language, items).run()
    summary = {"blocks": len(items), "words": sum(len(i.words) for i in items), "changed_by_code": sum(len(n) for n in result.notes.values()),
               "open_doubts": result.open_doubts}
    if result.open_doubts:
        raise AwaitingReadingReview(
            f"Awaiting reading review: {result.open_doubts} words of the {language} narration are in doubt. Answer them all in the Reading review "
            f"(GET /api/documentary/versions/{version.id}/reading-doubts), then resume. Nothing is voiced before.")
    return summary
