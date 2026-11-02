"""Who is wrong when two listeners hear a word differently from its reading: the voice, or the reading. A model decides with the sentence and the
English source; what it cannot tell stays for the owner."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.documentary.reading.ask import Runner, ask
from app.documentary.reading.block import BlockInput
from app.documentary.reading.inspector import Finding
from app.documentary.reading.table import FilmTable

WHO = ("voice", "reading", "unclear")


@dataclass(frozen=True)
class Ruling:
    finding: Finding
    who: str                # voice | reading | unclear
    latin: str              # the right reading, when who is "reading"
    why: str


class Judge:
    def __init__(self, db: Session, case_id: int, table: FilmTable, runner: Runner | None = None):
        self.db, self.case_id, self.table = db, case_id, table
        self.runner = runner or (lambda gen, body: run_agent("documentary.reading.judge", gen, body))

    async def rule(self, item: BlockInput, findings: list[Finding]) -> list[Ruling]:
        """A ruling for every finding of the block (a word the model does not answer stays unclear)."""
        if not findings:
            return []
        payload = {"film_table": self.table.describe(item.words), "english_source": item.english, "previous": item.previous, "sentence": item.sentence(),
                   "next": item.next, "words": [{"n": f.n, "word": f.surface, "written_reading": f.expected, "listener_heard": f.heard, "recogniser_heard": f.phones}
                                                for f in findings]}
        raw = await ask(self.db, self.case_id, f"Reading: judge {item.block_id}", payload, "reading_judge", self.runner)
        answers = {a.get("n"): a for a in raw.get("words") or [] if isinstance(a, dict)}
        rulings = []
        for f in findings:
            a = answers.get(f.n) or {}
            who = a.get("who") if a.get("who") in WHO else "unclear"
            latin = str(a.get("latin") or "").strip()
            rulings.append(Ruling(f, who if who != "reading" or latin else "unclear", latin if who == "reading" else "", str(a.get("why") or "")))
        return rulings
