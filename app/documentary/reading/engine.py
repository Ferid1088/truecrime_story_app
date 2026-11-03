"""The Persian Reading Engine: from the writer's readings to readings with a status, before the pronunciation engine (PPIE) uses them.

Persian text → numbered words → transcription check (format faults rewritten and recorded, sound doubts reported) → lexical evidence through the
morphology → the readings the evidence allows → contextual selection by a model that did not write the reading → a reading with a status:

- verified    the reading is among the evidence's and the word has one reading (or the model, from the sentence, chose the writer's own)
- resolved    the evidence allows other readings; the sentence's meaning and grammar picked one; it replaces the writer's
- uncertain   the evidence and the writer disagree and the sentence does not settle it, or the model is not sure
- unverified  no evidence exists for the word (a name, an unanalysed verb form); the writer's reading is kept, flagged, never promoted

The words, their order and the Persian text are never changed: every reading stays at the number of its word."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.reading.audit import Auditor, Finding
from app.documentary.reading.block import BlockInput
from app.documentary.reading.selector import Resolution, Selector, with_ezafe


@dataclass
class Outcome:
    block_id: str
    n: int
    surface: str
    writer: str
    reading: str
    status: str
    format_fixes: list[tuple[str, str]] = field(default_factory=list)
    sound_doubts: list[str] = field(default_factory=list)
    reason: str = ""
    candidates: list[str] = field(default_factory=list)
    basis: str = ""                    # what the evidence rests on: lexicon | rule | verb | exception
    homograph: bool = False
    conflict: bool = False             # the evidence did not allow the writer's reading


@dataclass
class EngineResult:
    outcomes: list[Outcome]

    def readings(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for o in self.outcomes:
            out.setdefault(o.block_id, []).append(o.reading)
        return out

    def summary(self) -> dict:
        counts = Counter(o.status for o in self.outcomes)
        return {"words": len(self.outcomes), "by_status": dict(counts), "format_fixed": sum(1 for o in self.outcomes if o.format_fixes),
                "sound_doubts": sum(1 for o in self.outcomes if o.sound_doubts), "changed": sum(1 for o in self.outcomes if o.reading != o.writer)}


class ReadingEngine:
    def __init__(self, auditor: Auditor, selector: Selector):
        self.auditor, self.selector = auditor, selector
        self.budget = ai_config.reading.selector_max_words

    def needs_choice(self, f: Finding) -> bool:
        homograph = f.status == "supported" and f.homograph and len(f.options) > 1 and ai_config.reading.select_supported_homographs
        return f.span == 1 and (f.status in ("vowel_conflict", "form_conflict") or homograph)

    async def block(self, item: BlockInput, readings: list[str]) -> list[Outcome]:
        out: list[Outcome] = []
        for f in self.auditor.block(item, readings):
            checked = self.auditor.tr.check(f.reading)
            fixed = checked.normalized
            base = dict(block_id=f.block_id, n=f.n, surface=f.surface, writer=f.reading, format_fixes=f.format_fixes, sound_doubts=f.sound_doubts,
                        candidates=f.options, basis=f.basis, homograph=f.homograph, conflict=f.status in ("vowel_conflict", "form_conflict"))
            if f.status == "number":
                out.append(Outcome(**base, reading=fixed, status="verified"))
            elif f.status == "no_evidence":
                out.append(Outcome(**base, reading=fixed, status="unverified", reason="no lexical evidence for this word"))
            elif f.basis == "exception" and f.status != "supported":
                out.append(Outcome(**base, reading=with_ezafe(f.options[0], fixed), status="resolved", reason="a verified exception (confirmed by a person)"))
            elif self.needs_choice(f) and self.budget > 0:
                self.budget -= 1
                got: Resolution = await self.selector.select(item, f)
                reading = got.reading if got.status == "resolved" else fixed
                out.append(Outcome(**base, reading=reading, status=got.status, reason=got.reason))
            elif self.needs_choice(f):
                out.append(Outcome(**base, reading=fixed, status="uncertain", reason="not sent to the selector: the run's budget of words is used"))
            elif f.status == "supported":
                out.append(Outcome(**base, reading=fixed, status="verified", reason="among the readings of the evidence"))
            else:
                out.append(Outcome(**base, reading=fixed, status="uncertain", reason="the evidence allows other readings of this phrase"))
        return out

    async def run(self, items: list[BlockInput], readings: dict[str, list[str]]) -> EngineResult:
        done: list[Outcome] = []
        for item in items:
            done += await self.block(item, readings[item.block_id])
        return EngineResult(done)
