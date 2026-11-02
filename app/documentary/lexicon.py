"""The pronunciation lexicon: how the voice must say a word, kept across films.

The listening review corrects a block by respelling the word it heard wrong; when the corrected block passes, that respelling is proven and is
kept here. Every film's pronunciation dictionary then starts from the lexicon, so the same mistake is not made twice. Only readings that do not
depend on the sentence are kept: a name or a word with its own vowels, never an ezafe (a word that is said differently in another sentence)."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import PronunciationLexicon

KASRA = "ِ"
EZAFE_MARKS = {KASRA, "ٔ", "‌"}              # the kasra, the hamza above and the half-space that join a word to the next
MARKS = re.compile("[ً-ْٰ]")
WORD = re.compile(r"[\w‌]+")


JOINERS = re.compile("[-\u2010\u2011\u2013\u2014\u203f]")


def plain_reading(reading: str) -> str:
    """A reading written as one word: no hyphen between its parts ("dær-e" -> "dære"). The voice may say a hyphen as a sound of its own
    ("dareye"), so a reading never carries one."""
    return JOINERS.sub("", reading)


def whole_word(surface: str) -> re.Pattern:
    return re.compile(rf"(?<![\w‌]){re.escape(surface)}(?![\w‌])")


def has_ezafe(surface: str) -> bool:
    """A written word that already carries the ezafe ("درِ", "فوریهٔ"): its reading is right only where the ezafe is."""
    return surface.endswith(KASRA) or "\u0654" in surface or "\u06c0" in surface


def is_ezafe_only(surface: str, reading: str) -> bool:
    """A reading that only adds the ezafe to the word ("شانزدهم" -> "شانزدهمِ"): it is right in one sentence and wrong in the next."""
    return reading.rstrip("".join(EZAFE_MARKS)) == surface and reading != surface


def learnable(surface: str, reading: str) -> bool:
    """Whether a respelling may be kept for every film: one word, really different from the word, not an ezafe."""
    return (len(WORD.findall(surface)) == 1 and surface != reading and not has_ezafe(surface) and not is_ezafe_only(surface, reading)
            and len(reading) <= 240 and bool(MARKS.sub("", reading).strip()))


def learn(db: Session, language: str, rules: list[dict], case_id: int | None, source: str = "reviewer") -> list[dict]:
    """Keeps the respellings `{"surface", "replacement"}` that may be kept. The same reading again counts a hit; an owner's reading is never
    replaced by a reviewer's. Returns the rules kept."""
    kept: list[dict] = []
    for rule in rules:
        surface, reading = rule["surface"], plain_reading(rule["replacement"])
        if not learnable(surface, reading):
            continue
        row = db.query(PronunciationLexicon).filter_by(language=language, surface=surface).first()
        if row is None:
            db.add(PronunciationLexicon(language=language, surface=surface, kind="alias", reading=reading, source=source, case_id=case_id))
        elif row.reading == reading:
            row.hits += 1
        elif row.source != "owner":
            row.kind, row.reading, row.source, row.hits, row.case_id = "alias", reading, source, 1, case_id
        else:
            continue
        kept.append({"surface": surface, "replacement": reading})
    db.commit()
    return kept


def confirm(db: Session, language: str, rules: list[dict], text: str, case_id: int | None) -> list[dict]:
    """The dictionary rules (ElevenLabs shape) of a film that were in the text of a block the listening review approved: the voice said these
    words right with them, so they are kept. Numbers are read by their own rule and never kept."""
    kept: list[dict] = []
    for rule in rules:
        surface, kind = rule["string_to_replace"], rule["type"]
        reading = plain_reading(rule.get("alias") if kind == "alias" else rule.get("phoneme") or "")
        if (not reading or re.fullmatch(r"[0-9۰-۹٠-٩]+", surface) or len(WORD.findall(surface)) != 1 or has_ezafe(surface)
                or not whole_word(surface).search(text) or len(reading) > 240):
            continue
        row = db.query(PronunciationLexicon).filter_by(language=language, surface=surface).first()
        if row is None:
            db.add(PronunciationLexicon(language=language, surface=surface, kind=kind, reading=reading, source="reviewer", case_id=case_id))
        elif (row.kind, row.reading) == (kind, reading):
            row.hits += 1
        else:
            continue                  # an earlier proven reading stays
        kept.append(rule)
    db.commit()
    return kept


def rules_for(db: Session, language: str, text: str) -> list[dict]:
    """The dictionary rules of the lexicon's words that occur in `text`."""
    rows = db.query(PronunciationLexicon).filter_by(language=language).all()
    return [{"string_to_replace": r.surface, "type": r.kind, **({"alias": r.reading} if r.kind == "alias" else {"alphabet": "ipa", "phoneme": r.reading})}
            for r in rows if whole_word(r.surface).search(text or "")]
