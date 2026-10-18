"""The names and places of the evidence and how each language spells them.

An entity is a person or a place a fact names. For every language the glossary lists the
spellings the owner accepts. The cross-language facts check requires that every entity of
an evidence item a text tells appears in that text in one of those spellings: a name that is
missing, or spelled in a way nobody approved, fails. Matching folds the script (Arabic and
Persian letter variants, diacritics, joiners) so only a real spelling difference counts."""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import Case, EntityGlossaryEntry
from app.language_pipeline.materials import SharedMaterial, dump
from app.language_pipeline.stages import language_name
from app.providers.generation import get_generation_provider
from app.services.tracking import documentary_run_context, stamp_run, track_run
from app.utils import utc_now

GLOSSARY_STAGE = "language_glossary"
PROPOSED, APPROVED = "proposed", "approved"

_FOLD = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "أ": "ا", "إ": "ا",
                       "آ": "ا", "ٱ": "ا", "ؤ": "و", "ئ": "ی", "‌": " ", "‍": "",
                       "ـ": ""})
_MARKS = re.compile(r"[ً-ٰٟۖ-ۭ]")
_LETTER = re.compile(r"\w", re.U)


def fold(text: str) -> str:
    """The text as letters only: one form per Arabic/Persian letter, no diacritics or
    joiners, lower case, single spaces."""
    text = unicodedata.normalize("NFKC", text or "").translate(_FOLD)
    text = _MARKS.sub("", text).casefold()
    return re.sub(r"[\W_]+", " ", text, flags=re.U).strip()


@dataclass(frozen=True)
class Entity:
    name: str
    kind: str
    evidence_ids: tuple[str, ...]


def entities_of(material: SharedMaterial) -> list[Entity]:
    """The proper-noun people and places of the evidence pack, each with the evidence ids that
    name it. A name that is only a part of a longer name of the same kind (a first name, a
    surname) is covered by the longer one."""
    cfg = ai_config.language_pipeline.cross_language.glossary
    found: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for fact in material.pack.get("facts") or []:
        for kind in cfg.kinds:
            for name in fact.get(kind) or []:
                name = " ".join(str(name).split())
                if len(name) < cfg.min_entity_chars or not all(
                        w[:1].isupper() or w[:1].isdigit() or w.casefold() in cfg.name_connectors
                        for w in name.replace(",", " ").split()):
                    continue   # a description ("Borden family home") is not a proper name
                _, ids = found.setdefault((kind, name.casefold()), (name, set()))
                ids.add(fact["id"])
    out = []
    for (kind, key), (name, ids) in found.items():
        words = set(key.replace(",", " ").split())
        inside = [other for (k, other) in found if k == kind and other != key
                  and words < set(other.replace(",", " ").split())]
        if not inside:
            out.append(Entity(name, kind, tuple(sorted(ids))))
    return sorted(out, key=lambda e: (e.kind, e.name))


def seed_spellings(entity: Entity) -> list[str]:
    """The evidence's own name and its natural short forms (a person: first + last name and
    surname; a place: the part before the comma)."""
    words = entity.name.replace(",", " ").split()
    forms = [entity.name]
    if entity.kind == "people" and len(words) >= 2:
        forms += [f"{words[0]} {words[-1]}", words[-1]]
    elif "," in entity.name:
        forms.append(entity.name.split(",")[0].strip())
    return list(dict.fromkeys(f for f in forms if len(f) >= 3))


class Glossary:
    """The stored entries of one case, read for the check and for the writers."""

    def __init__(self, entries: list[EntityGlossaryEntry]):
        self.entries = entries
        self._by = {(e.entity.casefold(), e.language): e for e in entries}

    def entry(self, entity: str, language: str) -> EntityGlossaryEntry | None:
        return self._by.get((entity.casefold(), language))

    @staticmethod
    def spellings_of(entry: EntityGlossaryEntry | None) -> list[str]:
        return json.loads(entry.spellings or "[]") if entry is not None else []

    def preferred(self, language: str) -> dict[str, str]:
        """For the writers: entity -> the first spelling listed for this language."""
        return {e.entity: s[0] for e in self.entries if e.language == language
                and (s := self.spellings_of(e))}

    def approved(self, language: str) -> bool:
        rows = [e for e in self.entries if e.language == language]
        return bool(rows) and all(e.status == APPROVED and self.spellings_of(e) for e in rows)


class GlossaryStore:
    def __init__(self, db: Session, case_id: int):
        self.db, self.case_id = db, case_id

    def load(self) -> Glossary:
        rows = (self.db.query(EntityGlossaryEntry)
                .filter(EntityGlossaryEntry.case_id == self.case_id)
                .order_by(EntityGlossaryEntry.kind, EntityGlossaryEntry.entity).all())
        return Glossary(rows)

    def sync(self, entities: list[Entity], languages: list[str]) -> int:
        """A row for every entity and language. New rows start as proposals (the evidence's own
        name for the seeded languages); rows that exist, and their approval, are kept."""
        cfg = ai_config.language_pipeline.cross_language.glossary
        have = {(e.entity.casefold(), e.language): e for e in self.load().entries}
        added = 0
        for entity in entities:
            for language in languages:
                row = have.get((entity.name.casefold(), language))
                ids = json.dumps(list(entity.evidence_ids))
                if row is not None:
                    row.evidence_ids = ids
                    continue
                seeded = language in cfg.seed_from_evidence
                self.db.add(EntityGlossaryEntry(
                    case_id=self.case_id, entity=entity.name, kind=entity.kind, language=language,
                    spellings=json.dumps(seed_spellings(entity) if seeded else [], ensure_ascii=False),
                    evidence_ids=ids, status=PROPOSED, origin="evidence" if seeded else "agent"))
                added += 1
        self.db.commit()
        return added

    def set_spellings(self, language: str, entity: str, spellings: list[str], by: str) -> EntityGlossaryEntry:
        row = (self.db.query(EntityGlossaryEntry).filter(
            EntityGlossaryEntry.case_id == self.case_id, EntityGlossaryEntry.language == language,
            EntityGlossaryEntry.entity == entity).first())
        if row is None:
            raise KeyError(f"no glossary entry {entity!r} in {language}")
        row.spellings = json.dumps([s.strip() for s in spellings if s.strip()], ensure_ascii=False)
        row.origin, row.status, row.approved_by, row.approved_at = "owner", PROPOSED, None, None
        self.db.commit()
        return row

    def approve(self, language: str, by: str) -> int:
        """Approves every entry of the language that has a spelling; an empty entry stays open."""
        count = 0
        for row in self.load().entries:
            if row.language == language and Glossary.spellings_of(row) and row.status != APPROVED:
                row.status, row.approved_by, row.approved_at = APPROVED, by, utc_now()
                count += 1
        self.db.commit()
        return count


class GlossaryProposer:
    """Asks the glossary agent for the standard spelling of each entity in a language. The
    proposal does not read the finished texts, so a misspelling in a text cannot become the
    standard; the owner approves the list in the UI."""

    def __init__(self, gen=None):
        self.gen = gen or get_generation_provider()

    async def propose(self, db: Session, case: Case, language: str) -> int:
        cfg = ai_config.language_pipeline.cross_language.glossary
        store = GlossaryStore(db, case.id)
        open_rows = [e for e in store.load().entries if e.language == language
                     and e.status != APPROVED and not Glossary.spellings_of(e)]
        filled = 0
        for start in range(0, len(open_rows), cfg.proposal_batch_entities):
            batch = open_rows[start:start + cfg.proposal_batch_entities]
            user = dump({"language": language, "language_name": language_name(language),
                         "entities": [{"name": r.entity, "kind": r.kind} for r in batch]})
            with documentary_run_context(
                    case_id=case.id, language=language, pipeline_stage=f"{GLOSSARY_STAGE}:{language}"), \
                    track_run(db, case.id, "Glossary Proposer",
                              input_summary=f"{language}: {len(batch)} entities") as run:
                data, res = await run_agent("language.glossary", self.gen, user,
                                            system=prompt("language_pipeline/glossary"))
                stamp_run(run, res, "evidence_normalizer")
            answers = {str(a.get("entity", "")).casefold(): a.get("spellings") or []
                       for a in data.get("entries") or [] if isinstance(a, dict)}
            for row in batch:
                spellings = [s for s in answers.get(row.entity.casefold(), []) if isinstance(s, str) and s.strip()]
                if spellings:
                    row.spellings = json.dumps(spellings, ensure_ascii=False)
                    filled += 1
            db.commit()
        return filled


@dataclass
class NameFinding:
    entity: str
    language: str
    problem: str          # missing | unlisted
    evidence_ids: list[str]
    nearest: str | None = None
    expected: list[str] = field(default_factory=list)


class NameCheck:
    """Does a text spell every entity of the evidence it tells in an approved way?"""

    def __init__(self, glossary: Glossary):
        self.glossary = glossary

    @staticmethod
    def _affixes(language: str) -> tuple[str, str]:
        cfg = ai_config.language_pipeline.cross_language.glossary
        prefixes = "|".join(re.escape(fold(p)) for p in cfg.attached_prefixes.get(language, []))
        suffixes = "|".join(re.escape(fold(s)) for s in cfg.attached_suffixes.get(language, []))
        return (f"(?:{prefixes})?" if prefixes else "", f"(?:{suffixes})?" if suffixes else "")

    def appears(self, text: str, spelling: str, language: str) -> bool:
        before, after = self._affixes(language)
        name = fold(spelling)
        if language in ai_config.language_pipeline.cross_language.space_free_names:
            name = r"\s?".join(re.escape(ch) for ch in name.replace(" ", ""))
        else:
            name = re.escape(name)
        pattern = rf"(?<!\w){before}{name}{after}(?!\w)"
        return re.search(pattern, fold(text), re.U) is not None

    @staticmethod
    def _nearest(text: str, spelling: str) -> str | None:
        """The stretch of the text that looks most like the spelling, for the repair note."""
        wanted = fold(spelling).split()
        words = fold(text).split()
        if not wanted or len(words) < len(wanted):
            return None
        grams = [" ".join(words[i:i + len(wanted)]) for i in range(len(words) - len(wanted) + 1)]
        close = difflib.get_close_matches(" ".join(wanted), grams, n=1, cutoff=0.7)
        return close[0] if close else None

    def findings(self, language: str, text: str, told_ids: set[str],
                 entities: list[Entity]) -> list[NameFinding]:
        found = []
        for entity in entities:
            ids = sorted(told_ids & set(entity.evidence_ids))
            if not ids:
                continue
            listed = Glossary.spellings_of(self.glossary.entry(entity.name, language))
            if not listed:
                found.append(NameFinding(entity.name, language, "unlisted", ids))
                continue
            if not any(self.appears(text, s, language) for s in listed):
                found.append(NameFinding(entity.name, language, "missing", ids,
                                         self._nearest(text, listed[0]), listed))
        return found
