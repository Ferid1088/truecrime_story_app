"""The earlier pronunciation systems (the owner's reading approvals and the lexicon the listening review learned) imported into the pronunciation memory.
Nothing is trusted because it was kept: every row becomes a legacy_unverified entry with its source, kind, reading, counters and dates as they were, and
waits in the review for a person to approve, correct or reject it. The tables they come from are not changed."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import PronunciationLexicon, ReadingApproval
from app.documentary.pronunciation_memory import PronunciationMemory
from app.documentary.pronunciation_trace import ipa_to_latin
from app.documentary.reading import spelling


def stamp(value) -> str | None:
    return value.isoformat() if value is not None else None


def persian_of_latin(latin: str) -> str:
    try:
        return spelling.persian(latin)
    except ValueError:
        return ""


def persian_of_ipa(ipa: str) -> str:
    latin = ipa_to_latin(ipa)
    return persian_of_latin(latin) if latin else ""


def import_legacy(db: Session, language: str | None = None) -> dict[str, int]:
    """Imports both tables, or those of one language (again without harm: a source already imported is skipped). Returns what was done."""
    memory, done = PronunciationMemory(db), {"entries_created": 0, "sources_added": 0, "skipped": 0}

    def put(row_source: dict, language: str, surface: str, expected: str, sense: str, case_id: int | None) -> None:
        entry, new = memory.add_legacy(language=language, surface=surface, expected_irab=expected, source=row_source, sense=sense, case_id=case_id)
        if not new:
            done["skipped"] += 1
        elif entry.version == 1:
            done["entries_created"] += 1
        else:
            done["sources_added"] += 1

    approvals, lexicon = db.query(ReadingApproval), db.query(PronunciationLexicon)
    if language:
        approvals, lexicon = approvals.filter_by(language=language), lexicon.filter_by(language=language)
    for r in approvals.order_by(ReadingApproval.id):
        put({"table": "reading_approvals", "id": r.id, "source": r.source, "kind": r.kind, "latin": r.latin, "case_id": r.case_id,
             "created_at": stamp(r.created_at), "updated_at": stamp(r.updated_at)}, r.language, r.surface, persian_of_latin(r.latin), r.sense, r.case_id)
    for r in lexicon.order_by(PronunciationLexicon.id):
        expected = r.reading if r.kind == "alias" else persian_of_ipa(r.reading)
        put({"table": "pronunciation_lexicon", "id": r.id, "source": r.source, "kind": r.kind, "reading": r.reading, "hits": r.hits, "case_id": r.case_id,
             "created_at": stamp(r.created_at), "updated_at": stamp(r.updated_at)}, r.language, r.surface, expected, "", r.case_id)
    return done
