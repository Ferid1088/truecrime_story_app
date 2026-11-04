"""The reading stage of a film's job: writes and checks the reading text of every block before the voice, and stops the run when words are in
doubt (the owner answers them all in one review, then resumes)."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, ReadingApproval, StoryVersion
from app.documentary import voice_prepare as VP
from app.documentary.performance import performance_for_version
from app.documentary.pipeline.errors import AwaitingReadingReview
from app.documentary.reading import consistency as CS
from app.documentary.reading.answer import ReadingWord
from app.documentary.reading.block import BlockInput, block_inputs
from app.documentary.reading.chooser import WordChooser
from app.documentary.reading.ezafe import Opinion
from app.documentary.reading.inspector import Inspector
from app.documentary.reading.loop import ReadingLoop
from app.documentary.reading.table import FilmTable
from app.documentary.reading.text import ReadingResult, ReadingStage
from app.documentary.reading.voicing import Voicing, WordSignal
from app.documentary.reading.words import key_of


def required(language: str) -> bool:
    return language in ai_config.reading.languages


async def result(db: Session, case: Case, version: StoryVersion, language: str) -> tuple[list[BlockInput], ReadingResult]:
    """The reading text of the version (a rerun costs only the blocks whose input changed)."""
    plan = performance_for_version(db, version)
    items = block_inputs(plan["blocks"], VP.english_sources(db, version), ai_config.semantic_review.neighbour_chars)
    return items, await ReadingStage(db, case.id, version, language, items).run()


async def voicing_of(db: Session, language: str, read: ReadingResult, items: list[BlockInput]) -> Voicing:
    fixed = {key_of(a.surface): a.latin for a in db.query(ReadingApproval).filter(ReadingApproval.language == language)}
    overridden = {block: {int(m.group(1)) for note in notes if (m := re.match(r"word (\d+)", note))} for block, notes in read.notes.items()}
    signals = {block: [WordSignal(r.confidence, r.n in read.doubts.get(block, set()), r.n in overridden.get(block, set())) for r in words]
               for block, words in read.readings.items()}
    names = names_of(read.table)
    by_block = {i.block_id: list(zip(i.words, [r.latin for r in read.readings[i.block_id]], strict=False)) for i in items if i.block_id in read.readings}
    return Voicing(fixed, {block: [r.latin for r in words] for block, words in read.readings.items()}, signals, names, opinions_of(read.readings),
                   CS.inconsistent(CS.variants(by_block, names)))


def names_of(table: FilmTable | None) -> frozenset[str]:
    """The words that are names wherever they stand: each part of a person's name, the film's foreign words and its one-word places. Not the owner's
    spoken forms of ordinary words, not a title (رئیس of "رئیس پلیس ...": the table keeps title + name as one entry, each name part has its own), and
    not the ordinary words of a long institution's name (the دادگاه of "دادگاه عمومیِ شهرستان میامی" is a court everywhere else)."""
    return frozenset(k for e in table.entries if e.kind == "foreign" or (e.kind in ("person", "place") and len(e.keys) == 1) for k in e.keys) if table else frozenset()


def opinions_of(readings: dict[str, list[ReadingWord]]) -> dict[str, dict[int, list[Opinion]]]:
    """Each agent's own ezafe judgement of each word, for the ezafe analyzer (a reading cache written before the agents gave one has none)."""
    out: dict[str, dict[int, list[Opinion]]] = {}
    for block, words in readings.items():
        for r in words:
            found = [o for source, raw in (r.ezafe or {}).items() if (o := Opinion.parse(source, raw)) is not None]
            if found:
                out.setdefault(block, {})[r.n] = found
    return out


async def voicing(db: Session, case: Case, version: StoryVersion, language: str) -> Voicing:
    """What the voice of this version is given for the words the owner has fixed."""
    items, read = await result(db, case, version, language)
    return await voicing_of(db, language, read, items)


async def run(db: Session, case: Case, version: StoryVersion, language: str) -> dict:
    items, read = await result(db, case, version, language)
    summary = {"blocks": len(items), "words": sum(len(i.words) for i in items), "changed_by_code": sum(len(n) for n in read.notes.values()),
               "open_doubts": read.open_doubts}
    if read.open_doubts:
        raise AwaitingReadingReview(
            f"Awaiting reading review: {read.open_doubts} words of the {language} narration are in doubt. Answer them all in the Reading Review tab, "
            "then resume. Nothing is voiced before.")
    return summary


async def listen_and_fix(db: Session, case: Case, version: StoryVersion, language: str, renderer, plan: dict, manifest: dict, folder, *, inspector=None,
                         choose_runner=None) -> dict:
    """The reading loop after the voice of a language: the narration is listened to, the words the voice said wrong are voiced again as IPA
    (only their blocks), and what could not be fixed is reported. Returns the manifest of the narration as it stands, with the report in it."""
    items, read = await result(db, case, version, language)
    voicing = await voicing_of(db, language, read, items)
    planner = getattr(renderer, "planner", None)
    loop = ReadingLoop(db, case.id, language, items, inspector or Inspector(WordChooser(db, case.id, choose_runner)), voicing,
                       (lambda block, surface, right: planner.learn(block, surface, heard_right=right, case_id=case.id)) if planner else None)
    renderer.reviewer = None                      # the whole-block review is over: a block voiced again is judged by the loop

    async def revoice(block_ids: list[str], round_no: int) -> dict:
        renderer.voicing = loop.voicing
        if round_no > 1:                          # the same text did not help: a retake
            for block in block_ids:
                renderer.seed_bumps[block] = renderer.seed_bumps.get(block, 0) + 1
        return await renderer.render(plan, case_id=case.id, story_version_id=version.id, force_block_ids=None)

    final = await loop.run(manifest, revoice)
    loop.save(folder)
    final["reading_loop"] = {"rounds": loop.report["rounds"], "fixed": len(loop.report["fixed"]), "unfixed": loop.report["unfixed"],
                             "weak_flags": len(loop.report["weak_flags"]), "unchecked_blocks": len(loop.report["unchecked_blocks"])}
    return final
