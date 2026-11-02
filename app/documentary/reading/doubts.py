"""The doubts of a film's reading text, kept in the database until the owner has answered all of them: the film is not voiced before."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ReadingApproval, ReadingDoubt, StoryVersion
from app.documentary.reading.checker import Doubt
from app.utils import utc_now


def sync(db: Session, case_id: int, version_id: int, found: dict[str, list[Doubt]], sentences: dict[str, str]) -> None:
    """The rows of this version's doubts follow what the reading found: a new doubt is added, an open one is refreshed, an open one that is no
    longer a doubt is removed, an answered one stays."""
    rows = {(r.block_id, r.word_n): r for r in db.query(ReadingDoubt).filter(ReadingDoubt.story_version_id == version_id).all()}
    wanted = {(block, d.n): d for block, items in found.items() for d in items}
    for (block, n), d in wanted.items():
        row = rows.get((block, n))
        if row is None:
            db.add(ReadingDoubt(case_id=case_id, story_version_id=version_id, block_id=block, word_n=n, surface=d.surface, sentence=sentences.get(block, ""),
                                question=d.question, editor_latin=d.editor_latin, checker_latin=d.checker_latin))
        elif row.status == "open":
            row.surface, row.sentence, row.question = d.surface, sentences.get(block, ""), d.question
            row.editor_latin, row.checker_latin = d.editor_latin, d.checker_latin
    for key, row in rows.items():
        if key not in wanted and row.status == "open":
            db.delete(row)
    db.commit()


def answered(db: Session, version_id: int) -> dict[tuple[str, int], tuple[str, str]]:
    """(block, word) -> (the word it was asked about, the owner's reading) for the doubts that are answered."""
    return {(r.block_id, r.word_n): (r.surface, r.answer_latin or "") for r in
            db.query(ReadingDoubt).filter(ReadingDoubt.story_version_id == version_id, ReadingDoubt.status == "answered").all()}


def open_rows(db: Session, version_id: int) -> list[ReadingDoubt]:
    return (db.query(ReadingDoubt).filter(ReadingDoubt.story_version_id == version_id, ReadingDoubt.status == "open")
            .order_by(ReadingDoubt.block_id, ReadingDoubt.word_n).all())


def answer(db: Session, row: ReadingDoubt, latin: str, everywhere: bool, language: str) -> ReadingDoubt:
    """The owner's reading of one doubt. With `everywhere` it is also kept for every film: the word is read this way wherever it stands."""
    row.status, row.answer_latin, row.answered_at = "answered", latin.strip(), utc_now()
    if everywhere:
        found = (db.query(ReadingApproval).filter_by(language=language, surface=row.surface, kind="spoken_form", sense="").first())
        if found is None:
            db.add(ReadingApproval(language=language, surface=row.surface, kind="spoken_form", sense="", latin=row.answer_latin, source="owner",
                                   case_id=row.case_id))
        else:
            found.latin = row.answer_latin
    db.commit()
    return row


class AnswerError(ValueError):
    """An answer that cannot be taken: a doubt that is not this film's, a choice without a reading."""


def view(row: ReadingDoubt) -> dict:
    return {"id": row.id, "block_id": row.block_id, "word_n": row.word_n, "surface": row.surface, "sentence": row.sentence,
            "question": row.question, "editor_latin": row.editor_latin, "checker_latin": row.checker_latin, "status": row.status,
            "answer_latin": row.answer_latin}


def state(db: Session, version: StoryVersion) -> dict:
    rows = (db.query(ReadingDoubt).filter(ReadingDoubt.story_version_id == version.id).order_by(ReadingDoubt.block_id, ReadingDoubt.word_n).all())
    return {"version_id": version.id, "language": version.language, "open": sum(r.status == "open" for r in rows),
            "answered": sum(r.status == "answered" for r in rows), "doubts": [view(r) for r in rows]}


def answer_all(db: Session, version: StoryVersion, answers: list[dict]) -> dict:
    """The owner's answers to a list of doubts in one go. Each is {"id", "choice": "editor" | "checker" | "custom", "latin", "everywhere"}.
    Nothing is saved when any of them cannot be taken."""
    rows = {r.id: r for r in db.query(ReadingDoubt).filter(ReadingDoubt.story_version_id == version.id).all()}
    taken: list[tuple[ReadingDoubt, str, bool]] = []
    for a in answers:
        row = rows.get(a.get("id"))
        if row is None:
            raise AnswerError(f"doubt {a.get('id')} is not a doubt of this version")
        latin = {"editor": row.editor_latin, "checker": row.checker_latin}.get(a.get("choice"), (a.get("latin") or "").strip())
        if a.get("choice") not in ("editor", "checker", "custom") or not latin:
            raise AnswerError(f"doubt {row.id} ({row.surface}): the choice {a.get('choice')!r} gives no reading")
        taken.append((row, latin, bool(a.get("everywhere"))))
    for row, latin, everywhere in taken:
        answer(db, row, latin, everywhere, version.language)
    return state(db, version)
