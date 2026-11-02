"""The checker of the reading loop: another model judges the editor's reading of a block word by word; a word it rejects goes to an arbiter that
sees both proofs, and a word nobody can settle becomes a doubt for the owner. A reading the film table fixes is never questioned."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.documentary.reading import scheme
from app.documentary.reading.answer import ReadingWord
from app.documentary.reading.ask import Runner, ask
from app.documentary.reading.block import BlockInput
from app.documentary.reading.table import FilmTable
from app.documentary.reading.writer import context


@dataclass(frozen=True)
class Doubt:
    n: int
    surface: str
    question: str
    editor_latin: str
    checker_latin: str = ""


class Checker:
    def __init__(self, db: Session, case_id: int, table: FilmTable, check_runner: Runner | None = None, arbitrate_runner: Runner | None = None):
        self.db, self.case_id, self.table = db, case_id, table
        self.check_runner = check_runner or (lambda gen, body: run_agent("documentary.reading.check", gen, body))
        self.arbitrate_runner = arbitrate_runner or (lambda gen, body: run_agent("documentary.reading.arbitrate", gen, body))

    async def check(self, item: BlockInput, readings: list[ReadingWord]) -> tuple[list[ReadingWord], list[Doubt]]:
        """(the readings after the checker and the arbiter, the doubts left)."""
        payload = {**context(item, self.table), "reading": [{"n": r.n, "latin": r.latin, "why": r.why, "confidence": r.confidence} for r in readings]}
        raw = await ask(self.db, self.case_id, f"Reading: check {item.block_id}", payload, "reading_checker", self.check_runner)
        fixed = self.table.fixed_readings(item.words)
        by_n = {r.n: r for r in readings}
        out, doubts, seen = dict(by_n), [], set()
        for finding in raw.get("words") or []:
            n = finding.get("n") if isinstance(finding, dict) else None
            if n not in by_n or n in fixed or n in seen or item.words[n].number:
                continue
            seen.add(n)
            mine, word = by_n[n], item.words[n]
            if finding.get("verdict") == "doubt":
                doubts.append(Doubt(n, word.surface, str(finding.get("question") or ""), mine.latin))
                continue
            proposed = scheme.split_pause(str(finding.get("proposed_latin") or ""))[0]
            if not proposed or scheme.same_reading(proposed, mine.latin):
                continue
            verdict = await self.arbitrate(item, mine, finding, proposed)
            choice = verdict.get("decision")
            if choice == "checker":
                out[n] = replace(mine, latin=proposed)
            elif choice == "other" and verdict.get("latin"):
                out[n] = replace(mine, latin=scheme.split_pause(str(verdict["latin"]))[0])
            elif choice != "editor":
                doubts.append(Doubt(n, word.surface, str(verdict.get("question") or finding.get("proof") or ""), mine.latin, proposed))
        return [out[r.n] for r in readings], doubts

    async def arbitrate(self, item: BlockInput, mine: ReadingWord, finding: dict, proposed: str) -> dict:
        payload = {"film_table": self.table.describe(item.words), "english_source": item.english, "previous": item.previous, "sentence": item.sentence(),
                   "next": item.next, "word": item.words[mine.n].surface,
                   "editor": {"latin": mine.latin, "why": mine.why}, "checker": {"latin": proposed, "proof": str(finding.get("proof") or "")}}
        return await ask(self.db, self.case_id, f"Reading: arbitrate {item.block_id} word {mine.n}", payload, "reading_arbiter", self.arbitrate_runner)
