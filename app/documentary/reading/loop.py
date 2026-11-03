"""The reading loop after the voice. Every voiced block is scored by the phoneme recogniser and the words a wrong form fits better are checked by a
model that hears the word's clip (inspector.py). The reading text is right, so a word found wrong is a voice fault: it is given to the voice as IPA
and the block is voiced again, up to `max_rounds` times (a retake changes the seed). What stays wrong is reported. Only the failing blocks are
voiced again. A word that is right after the loop is kept for every film of the language."""

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
from app.documentary.reading.scheme import split_ezafe, split_pause
from app.documentary.reading.voicing import Voicing
from app.documentary.reading.words import key_of

log = logging.getLogger(__name__)


def stem_of(reading: str) -> str:
    return split_ezafe(split_pause(reading)[0])[0]


class ReadingLoop:
    def __init__(self, db: Session, case_id: int, language: str, items: list[BlockInput], inspector: Inspector, voicing: Voicing, on_result=None):
        self.db, self.case_id, self.language = db, case_id, language
        self.on_result = on_result                              # (block_id, surface, heard_right): what the listening found, for the pronunciation planner
        self.items = {i.block_id: i for i in items}
        self.inspector, self.voicing = inspector, voicing
        self.readings = voicing.readings                       # one dictionary: the reading the voice is given
        self.report: dict = {"rounds": [], "fixed": [], "unfixed": [], "weak_flags": [], "unchecked_blocks": []}

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
        failing: dict[tuple[str, int], Finding] = {}
        for round_no in range(ai_config.reading.max_rounds + 1):
            found, weak = await self.inspect(manifest, todo)
            if round_no == 0:
                self.report["weak_flags"] = [self.describe(f) for flags in weak.values() for f in flags]
            now = {(f.block_id, f.n): f for flags in found.values() for f in flags}
            self.report["rounds"].append({"round": round_no, "blocks_listened": len(todo), "faults": len(now)})
            self.settle(failing, now)
            if self.on_result:
                for f in now.values():
                    self.on_result(f.block_id, f.surface, False)
            failing = now
            if not failing or round_no == ai_config.reading.max_rounds:
                break
            self.voicing = self.voicing.with_fixed({key_of(f.surface): stem_of(f.expected) for f in failing.values()})
            todo = sorted({block for block, _ in failing})
            manifest = await revoice(todo, round_no + 1)
        self.report["unfixed"] = [self.describe(f) for f in failing.values()]
        return manifest

    def settle(self, failing: dict, now: dict) -> None:
        """A word that failed in the round before and is not found now is fixed: it is kept for every film."""
        for key, f in failing.items():
            if key not in now:
                self.keep(f.surface, stem_of(f.expected))
                self.report["fixed"].append({"block": f.block_id, "word": f.surface, "reading": f.expected})
                if self.on_result:
                    self.on_result(f.block_id, f.surface, True)

    @staticmethod
    def describe(f: Finding) -> dict:
        return {"block": f.block_id, "word": f.surface, "reading": f.expected, "heard": f.said, "best_fit": f.best, "margin": f.margin}

    def save(self, folder: Path) -> Path:
        path = folder / "reading_loop.json"
        path.write_text(json.dumps(self.report, ensure_ascii=False, indent=1), encoding="utf-8")
        return path
