"""The reading loop after the voice. Every voiced block is listened to (blind listener + phoneme recogniser) and compared with its reading; a judge
says for each finding who is wrong. A word the voice said wrong is given to the voice as IPA and the block is voiced again, up to `max_rounds`
times (a retake changes the seed); a word the reading had wrong is fixed in the reading; what stays wrong or unclear is reported. Only the
failing blocks are voiced again. A word that is right after the loop is kept for every film of the language."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import ReadingApproval
from app.documentary.reading.block import BlockInput
from app.documentary.reading.inspector import Finding, Inspector
from app.documentary.reading.judge import Judge, Ruling
from app.documentary.reading.scheme import split_ezafe, split_pause
from app.documentary.reading.voicing import Voicing
from app.documentary.reading.words import key_of, tokenize

log = logging.getLogger(__name__)


def stem_of(reading: str) -> str:
    return split_ezafe(split_pause(reading)[0])[0]


class ReadingLoop:
    def __init__(self, db: Session, case_id: int, language: str, items: list[BlockInput], inspector: Inspector, judge: Judge, voicing: Voicing):
        self.db, self.case_id, self.language = db, case_id, language
        self.items = {i.block_id: i for i in items}
        self.inspector, self.judge, self.voicing = inspector, judge, voicing
        self.readings = voicing.readings                       # one dictionary: a reading the judge corrects is the one the voice is given
        self.report: dict = {"rounds": [], "fixed": [], "unfixed": [], "reading_fixed": [], "unclear": [], "weak_flags": [], "unchecked_blocks": []}

    def checkable(self, block_id: str) -> bool:
        item = self.items.get(block_id)
        return item is not None and len(item.words) == len(self.readings.get(block_id, []))

    async def inspect(self, manifest: dict, block_ids: list[str]) -> tuple[dict[str, list[Finding]], dict[str, list[Finding]]]:
        """(the findings, the weak flags) of the listed blocks of the narration of `manifest`."""
        from app.documentary.voice_render import ROOT

        spoken_by_block: dict[str, list[dict]] = {}
        for w in manifest["timeline"]["words"]:
            spoken_by_block.setdefault(w["block_id"], []).append(w)
        blocks = {b["block_id"]: b for b in manifest["timeline"]["blocks"]}
        gate = asyncio.Semaphore(ai_config.reading.listen_parallel_blocks)

        async def one(block_id: str) -> tuple[str, tuple[list[Finding], list[Finding]]]:
            block = blocks[block_id]
            spoken = [{**w, "start": w["start"] - block["start"], "end": w["end"] - block["start"]} for w in spoken_by_block.get(block_id, [])]
            async with gate:
                return block_id, await self.inspector.inspect(block_id, self.items[block_id].text, self.readings[block_id], Path(ROOT) / block["wav"], spoken)

        done = dict(await asyncio.gather(*(one(b) for b in block_ids)))
        return {b: f for b, (f, _) in done.items()}, {b: w for b, (_, w) in done.items()}

    async def rule(self, found: dict[str, list[Finding]]) -> list[Ruling]:
        done = await asyncio.gather(*(self.judge.rule(self.items[b], f) for b, f in found.items() if f))
        return [r for rulings in done for r in rulings]

    def keep(self, surface: str, latin: str, source: str = "listener") -> None:
        """A word fixed for every film of the language."""
        row = self.db.query(ReadingApproval).filter_by(language=self.language, surface=key_of(surface), kind="spoken_form", sense="").one_or_none()
        if row is None:
            row = ReadingApproval(language=self.language, surface=key_of(surface), kind="spoken_form", sense="", source=source, case_id=self.case_id)
            self.db.add(row)
        row.latin = latin
        self.db.commit()

    async def run(self, manifest: dict, revoice) -> dict:
        """`revoice(block_ids, round)` voices those blocks again (with `self.voicing` as it is now) and returns the new manifest of the narration."""
        every = [b["block_id"] for b in manifest["timeline"]["blocks"] if b["block_id"] in self.items]
        self.report["unchecked_blocks"] = [b for b in every if not self.checkable(b)]
        todo = [b for b in every if self.checkable(b)]
        failing: dict[tuple[str, int], Ruling] = {}
        for round_no in range(ai_config.reading.max_rounds + 1):
            found, weak = await self.inspect(manifest, todo)
            rulings = await self.rule(found)
            if round_no == 0:
                self.report["weak_flags"] = [{"block": f.block_id, "word": f.surface, "reading": f.expected, "listener": f.heard, "recogniser": f.phones}
                                             for flags in weak.values() for f in flags]
                self.apply_reading_rulings(rulings)
                self.report["unclear"] = [self.describe(r) for r in rulings if r.who == "unclear"]
            now = {(r.finding.block_id, r.finding.n): r for r in rulings if r.who == "voice"}
            self.record(round_no, found, rulings, now)
            self.settle(failing, now)
            failing = now
            if not failing or round_no == ai_config.reading.max_rounds:
                break
            self.voicing = self.voicing.with_fixed({key_of(r.finding.surface): stem_of(r.finding.expected) for r in failing.values()})
            todo = sorted({block for block, _ in failing})
            manifest = await revoice(todo, round_no + 1)
        self.report["unfixed"] = [self.describe(r) for r in failing.values()]
        return manifest

    def settle(self, failing: dict, now: dict) -> None:
        """A word that failed in the round before and is not found now is fixed: it is kept for every film."""
        for key, ruling in failing.items():
            if key not in now:
                f = ruling.finding
                self.keep(f.surface, stem_of(f.expected))
                self.report["fixed"].append({"block": f.block_id, "word": f.surface, "reading": f.expected})

    def apply_reading_rulings(self, rulings: list[Ruling]) -> None:
        for r in rulings:
            if r.who != "reading":
                continue
            f = r.finding
            self.readings[f.block_id][f.n] = r.latin
            if stem_of(r.latin) != stem_of(f.expected):         # a different sound is kept for every film; a missing ezafe belongs to this sentence
                self.keep(f.surface, stem_of(r.latin))
            self.report["reading_fixed"].append({**self.describe(r), "new_reading": r.latin})

    @staticmethod
    def describe(r: Ruling) -> dict:
        f = r.finding
        return {"block": f.block_id, "word": f.surface, "reading": f.expected, "listener": f.heard, "recogniser": f.phones, "why": r.why}

    def record(self, round_no: int, found: dict[str, list[Finding]], rulings: list[Ruling], now: dict) -> None:
        self.report["rounds"].append({"round": round_no, "blocks_listened": len(found), "findings": sum(len(f) for f in found.values()),
                                      "voice_faults": len(now), "reading_faults": sum(r.who == "reading" for r in rulings),
                                      "unclear": sum(r.who == "unclear" for r in rulings)})

    def save(self, folder: Path) -> Path:
        path = folder / "reading_loop.json"
        path.write_text(json.dumps(self.report, ensure_ascii=False, indent=1), encoding="utf-8")
        return path
