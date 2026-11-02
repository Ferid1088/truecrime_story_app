"""The reading stage of a film's job: writes and checks the reading text of every block before the voice, and stops the run when words are in
doubt (the owner answers them all in one review, then resumes)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, ReadingApproval, StoryVersion
from app.documentary import voice_prepare as VP
from app.documentary.performance import performance_for_version
from app.documentary.pipeline.errors import AwaitingReadingReview
from app.documentary.reading.block import BlockInput, block_inputs
from app.documentary.reading.text import ReadingResult, ReadingStage
from app.documentary.reading.voicing import Voicing
from app.documentary.reading.words import key_of


def required(language: str) -> bool:
    return language in ai_config.reading.languages


async def result(db: Session, case: Case, version: StoryVersion, language: str) -> tuple[list[BlockInput], ReadingResult]:
    """The reading text of the version (a rerun costs only the blocks whose input changed)."""
    plan = performance_for_version(db, version)
    items = block_inputs(plan["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
    return items, await ReadingStage(db, case.id, version, language, items).run()


async def voicing(db: Session, case: Case, version: StoryVersion, language: str) -> Voicing:
    """What the voice of this version is given for the words the owner has fixed."""
    _, read = await result(db, case, version, language)
    fixed = {key_of(a.surface): a.latin for a in db.query(ReadingApproval).filter(ReadingApproval.language == language)}
    return Voicing(fixed, {block: [r.latin for r in words] for block, words in read.readings.items()})


async def run(db: Session, case: Case, version: StoryVersion, language: str) -> dict:
    items, read = await result(db, case, version, language)
    summary = {"blocks": len(items), "words": sum(len(i.words) for i in items), "changed_by_code": sum(len(n) for n in read.notes.values()),
               "open_doubts": read.open_doubts}
    if read.open_doubts:
        raise AwaitingReadingReview(
            f"Awaiting reading review: {read.open_doubts} words of the {language} narration are in doubt. Answer them all in the Reading Review tab, "
            "then resume. Nothing is voiced before.")
    return summary
