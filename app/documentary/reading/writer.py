"""The editor of the reading loop: writes the reading text of one block (a Latin reading for every numbered word). An answer that does not have the
words of the block, once each, in their order is refused and the editor is asked again with the problems; the film table is enforced by code."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.documentary.reading import answer as ANS
from app.documentary.reading.ask import ReadingFailed, Runner, ask
from app.documentary.reading.block import BlockInput
from app.documentary.reading.table import FilmTable


def context(item: BlockInput, table: FilmTable) -> dict:
    return {"film_table": table.describe(item.words), "english_source": item.english, "previous": item.previous, "next": item.next,
            "words": [{"n": w.n, "text": w.plain} for w in item.words]}


class Writer:
    def __init__(self, db: Session, case_id: int, table: FilmTable, runner: Runner | None = None):
        self.db, self.case_id, self.table = db, case_id, table
        self.runner = runner or (lambda gen, body: run_agent("documentary.reading.write", gen, body))

    async def write(self, item: BlockInput) -> tuple[list[ANS.ReadingWord], list[str]]:
        """(the readings, the notes of what code changed). Raises ReadingFailed when the answers stay wrong after the allowed retries."""
        cfg = ai_config.reading
        problems: list[str] = []
        for _attempt in range(cfg.max_writer_retries + 1):
            payload = {**context(item, self.table), **({"problems": problems} if problems else {})}
            raw = await ask(self.db, self.case_id, f"Reading: write {item.block_id}", payload, "reading_editor", self.runner)
            try:
                readings = ANS.parse(raw, item.words)
            except ANS.ReadingShapeError as e:
                problems = e.problems
                continue
            return ANS.enforce(readings, item.words, self.table, cfg.pause_between_names)
        raise ReadingFailed(f"the reading of {item.block_id} stays wrong after {cfg.max_writer_retries} retries: {'; '.join(problems[:4])}")
