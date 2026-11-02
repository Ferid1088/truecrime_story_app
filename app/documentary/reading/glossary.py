"""The reader: builds the film's name table from the whole Persian text, a chunk of blocks at a time (each chunk is told what the earlier ones found,
so a person keeps one id), and the doubts it could not settle."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.documentary.reading.ask import Runner, ask
from app.documentary.reading.table import FilmTable, FilmTableError
from app.documentary.reading.words import key_of

PEOPLE = "people"


def person_key(person: dict) -> tuple[str, str]:
    return (key_of((person.get("first") or {}).get("fa", "")), key_of((person.get("last") or {}).get("fa", "")))


def merge(known: dict, reply: dict) -> dict:
    """The table so far with one chunk's answer added: a person already known (the same first and last name, or the same id) gets the new titles
    and parts; places and foreign words are kept once."""
    table = {PEOPLE: [dict(p) for p in known.get(PEOPLE, [])], "places": list(known.get("places", [])), "foreign": list(known.get("foreign", []))}
    for person in reply.get(PEOPLE) or []:
        if not isinstance(person, dict) or not person.get("first") and not person.get("last"):
            continue
        same = next((p for p in table[PEOPLE] if p.get("id") == person.get("id") or person_key(p) == person_key(person)), None)
        if same is None:
            table[PEOPLE].append({**person, "titles": list(person.get("titles") or [])})
            continue
        for part in ("first", "last"):
            if not same.get(part) and person.get(part):
                same[part] = person[part]
        titles = {key_of(t["fa"]) for t in same.get("titles") or []}
        same["titles"] = list(same.get("titles") or []) + [t for t in person.get("titles") or [] if key_of(t.get("fa", "")) not in titles]
    for kind in ("places", "foreign"):
        have = {key_of(x["fa"]) for x in table[kind]}
        table[kind] += [x for x in reply.get(kind) or [] if isinstance(x, dict) and x.get("fa") and x.get("latin") and key_of(x["fa"]) not in have]
    return table


class Glossary:
    def __init__(self, db: Session, case_id: int, runner: Runner | None = None):
        self.db, self.case_id = db, case_id
        self.runner = runner or (lambda gen, body: run_agent("documentary.reading.glossary", gen, body))

    async def build(self, blocks: list[dict]) -> tuple[dict, list[dict]]:
        """(the reader's table, its doubts). `blocks` are {"block", "english", "text"}. The chunks are asked one after the other, because each is
        given the table of the earlier ones."""
        size = ai_config.reading.chunk_blocks
        table: dict = {PEOPLE: [], "places": [], "foreign": []}
        doubts: list[dict] = []
        for start in range(0, len(blocks), size):
            chunk = blocks[start:start + size]
            payload = {"blocks": [{"block": b["block"], "text": b["text"]} for b in chunk],
                       "english": "\n\n".join(dict.fromkeys(b["english"] for b in chunk if b["english"])), "known": table}
            reply = await ask(self.db, self.case_id, f"Reading: names ({start // size + 1})", payload, "reading_reader", self.runner)
            table = merge(table, reply)
            doubts += [d for d in reply.get("doubts") or [] if isinstance(d, dict) and d.get("word")]
        try:
            FilmTable.build(table)
        except FilmTableError as e:
            from app.documentary.reading.ask import ReadingFailed

            raise ReadingFailed(f"the reader's name table is wrong: {e}") from e
        return table, doubts
