"""The reading text of a film: the name table, then for every block the editor's reading, the checker's judgement and the arbiter's decision; what
nobody can settle is a doubt for the owner. Every step is kept on disk by its input, so a rerun (after the owner has answered) only pays for the
blocks whose input changed."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.prompts import prompt_sha256
from app.db.models import ReadingApproval, StoryVersion
from app.documentary.reading import doubts as DB
from app.documentary.reading.answer import ReadingWord
from app.documentary.reading.block import BlockInput
from app.documentary.reading.checker import Checker, Doubt
from app.documentary.reading.glossary import Glossary
from app.documentary.reading.table import FilmTable
from app.documentary.reading.writer import Writer, context

PROMPTS = ("glossary", "write", "check", "arbitrate")


@dataclass
class ReadingResult:
    readings: dict[str, list[ReadingWord]]
    open_doubts: int
    notes: dict[str, list[str]] = field(default_factory=dict)
    table: FilmTable | None = None
    doubts: dict[str, set[int]] = field(default_factory=dict)          # block -> the words the checker and the arbiter left in doubt


def sha(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def prompts_sha() -> str:
    return sha([prompt_sha256(f"documentary/reading/{name}_system") for name in PROMPTS])


class ReadingStage:
    def __init__(self, db: Session, case_id: int, version: StoryVersion, language: str, items: list[BlockInput], glossary: Glossary | None = None,
                 writer_runner=None, check_runner=None, arbitrate_runner=None):
        from app.documentary.voice_render import ROOT

        self.db, self.case_id, self.version, self.language, self.items = db, case_id, version, language, items
        self.glossary = glossary or Glossary(db, case_id)
        self.runners = (writer_runner, check_runner, arbitrate_runner)
        base = Path(ai_config.voice.work_dir)
        self.path = (base if base.is_absolute() else ROOT / base) / str(case_id) / ai_config.reading.folder / f"{language}_v{version.id}.json"
        try:
            self.cache: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")

    def approvals(self) -> list[dict]:
        rows = self.db.query(ReadingApproval).filter(ReadingApproval.language == self.language).all()
        return [{"surface": r.surface, "latin": r.latin, "kind": r.kind} for r in rows]

    async def reader_table(self) -> tuple[dict, list[dict]]:
        key = sha([(i.block_id, i.text) for i in self.items], prompts_sha(), ai_config.reading.chunk_blocks)
        if self.cache.get("reader_key") == key:
            return self.cache["reader"], self.cache["reader_doubts"]
        table, doubts = await self.glossary.build([{"block": i.block_id, "english": i.english, "text": i.text} for i in self.items])
        self.cache.update({"reader_key": key, "reader": table, "reader_doubts": doubts})
        self.save()
        return table, doubts

    async def block(self, item: BlockInput, table: FilmTable, gate: asyncio.Semaphore) -> tuple[list[ReadingWord], list[Doubt], list[str]]:
        key = sha(context(item, table), prompts_sha(), ai_config.reading.pause_between_names)
        saved = self.cache.setdefault("blocks", {}).get(item.block_id)
        if saved and saved["key"] == key:
            return [ReadingWord(**r) for r in saved["readings"]], [Doubt(**d) for d in saved["doubts"]], saved["notes"]
        async with gate:
            readings, notes = await Writer(self.db, self.case_id, table, self.runners[0]).write(item)
            readings, doubts = await Checker(self.db, self.case_id, table, self.runners[1], self.runners[2]).check(item, readings)
        self.cache["blocks"][item.block_id] = {"key": key, "readings": [asdict(r) for r in readings], "doubts": [asdict(d) for d in doubts], "notes": notes}
        self.save()
        return readings, doubts, notes

    @staticmethod
    def reader_doubts(item: BlockInput, found: list[dict], readings: list[ReadingWord]) -> list[Doubt]:
        """The reader's doubts about a word of this block, as doubts at the word's place."""
        out = []
        for d in found:
            if d.get("block") != item.block_id:
                continue
            word = next((w for w in item.words if w.key == d["word"].replace("‌", "")), None)
            if word is not None and not word.number:           # digits are read by code: there is nothing to ask
                out.append(Doubt(word.n, word.surface, str(d.get("question") or ""), readings[word.n].latin))
        return out

    async def run(self) -> ReadingResult:
        reader, reader_found = await self.reader_table()
        table = FilmTable.build(reader, self.approvals())
        gate = asyncio.Semaphore(ai_config.reading.parallel_blocks)
        done = await asyncio.gather(*(self.block(i, table, gate) for i in self.items))
        found: dict[str, list[Doubt]] = {}
        readings: dict[str, list[ReadingWord]] = {}
        notes: dict[str, list[str]] = {}
        for item, (words, doubts, note) in zip(self.items, done, strict=True):
            extra = [d for d in self.reader_doubts(item, reader_found, words) if d.n not in {x.n for x in doubts}]
            found[item.block_id], readings[item.block_id], notes[item.block_id] = doubts + extra, words, note
        DB.sync(self.db, self.case_id, self.version.id, {b: d for b, d in found.items() if d}, {i.block_id: i.sentence() for i in self.items})
        given = DB.answered(self.db, self.version.id)
        for item in self.items:
            for r in list(readings[item.block_id]):
                hit = given.get((item.block_id, r.n))
                if hit and hit[0] == item.words[r.n].surface:
                    readings[item.block_id][r.n] = ReadingWord(r.n, hit[1], r.pause_after, r.why, "high")
        return ReadingResult(readings, len(DB.open_rows(self.db, self.version.id)), notes, table, {b: {d.n for d in d_} for b, d_ in found.items() if d_})
