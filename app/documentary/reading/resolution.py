"""The resolution of a word the film reads in more than one way, by stated rules and evidence only, never by how often a reading occurs or how sure a model
was. In order:

  owner            the owner's approval (it is applied as an approval, not stored here);
  homograph        a homograph or a listed free variant: each sentence keeps its reading (retained, not resolved);
  source_syllables a name whose source spelling (the film table's person id, from the English story) has as many syllables as exactly one candidate;
  orthography      exactly one candidate's sounds can be written on the word's own letters: the approved text decides;
  enclitic         candidates that differ only in the vowel before a possessive or plural enclitic (-esh / -ash): both are Tehran speech (retained);
  (none)           the evidence does not decide: the family goes to the owner, with each candidate's evidence and where the film reads it.

A resolution sets the reading of the written forms it names for one case. It reaches the blocks through the film table (kind "resolved"), so only the
blocks that contain a resolved word are read again."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import PronunciationLexicon, ReadingResolution
from app.documentary.reading import consistency as CS
from app.documentary.reading import reference as REF
from app.documentary.reading.words import Word, key_of, tokenize

ENCLITIC = re.compile(r"[ea]?(shun|mun|tun|sh|m|t)$")
VOWEL_GROUPS = re.compile(r"[aeiouy]+")


@dataclass
class Family:
    key: str
    kind: str                                             # name | word
    candidates: dict[str, list[str]]                      # normalised reading -> blocks
    forms: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)    # written key -> (block, surface, reading) of every occurrence
    status: str = "unresolved"                            # resolved | retained_variant | unresolved
    rule: str = ""
    reading: str = ""
    evidence: str = ""
    why_undecided: str = ""

    def describe(self) -> dict:
        return {"word": self.key, "kind": self.kind, "status": self.status, "rule": self.rule, "reading": self.reading, "evidence": self.evidence,
                "candidates": {r: len(b) for r, b in self.candidates.items()}, "occurrences": sum(len(v) for v in self.forms.values()),
                "forms": sorted(self.forms), "why_undecided": self.why_undecided}


def english_syllables(name: str) -> int:
    """The syllables of an English name by its vowel groups (a final silent e is not one: Kate)."""
    word = name.lower()
    if word.endswith("e") and len(word) > 3 and word[-2] not in "aeiouy":
        word = word[:-1]
    return len(VOWEL_GROUPS.findall(word))


def letters_fit(key: str, reading: str) -> bool:
    """Whether the reading's sounds can be written on these letters, by the reference generator and its independent validator."""
    word = tokenize(key)[0]
    ref = REF.reference_of(word, reading)
    return ref is not None and ref.valid


def resolve(families: list[Family], fixed: dict[str, str], sources: dict[str, str]) -> list[Family]:
    """Applies the rules to every family (in place) and returns them. `sources`: a name's written key -> its source spelling (the film table's id)."""
    free = {key_of(w) for w in ai_config.homographs.words} | {key_of(w) for w in ai_config.homographs.free_variants}
    for f in families:
        readings = list(f.candidates)
        if f.key in fixed:
            f.status, f.rule, f.reading, f.evidence = "resolved", "owner", fixed[f.key], "the owner's approval"
            continue
        if f.key in free and f.kind == "word":
            listed = {CS.normal(r) for w, rs in {**ai_config.homographs.words, **ai_config.homographs.free_variants}.items() if key_of(w) == f.key for r in rs}
            off = [r for r in readings if r not in listed]
            if not off:
                f.status, f.rule, f.evidence = "retained_variant", "homograph", "a homograph or a listed free variant: each sentence keeps its reading"
                continue
            f.evidence = f"a homograph, but {', '.join(off)} is none of its readings ({', '.join(sorted(listed))})"
        if f.kind == "name" and f.key in sources:
            want = english_syllables(sources[f.key])
            fits = [r for r in readings if len(REF.syllables_of(REF.phonemes_of(r))[0]) == want]
            if len(fits) == 1:
                f.status, f.rule, f.reading = "resolved", "source_syllables", fits[0]
                f.evidence = (f"the source name {sources[f.key]!r} has {want} syllable(s); {fits[0]} has {want}, "
                              + ", ".join(f"{r} has {len(REF.syllables_of(REF.phonemes_of(r))[0])}" for r in readings if r != fits[0]))
                continue
        fitting = [r for r in readings if letters_fit(f.key, r)]
        if len(fitting) == 1:
            f.status, f.rule, f.reading = "resolved", "orthography", fitting[0]
            f.evidence = f"only {fitting[0]} can be written on the approved text's letters {f.key}; " + ", ".join(
                f"{r} cannot" for r in readings if r != fitting[0])
            continue
        if len({ENCLITIC.sub(lambda m: m.group(1), r) for r in readings}) == 1 and all(ENCLITIC.search(r) for r in readings):
            f.status, f.rule = "retained_variant", "enclitic"
            f.evidence = "the candidates differ only in the vowel before an enclitic (-esh / -ash, -eshun / -shun): both are said in Tehran speech"
            continue
        f.why_undecided = ("every candidate can be written on the letters" if len(fitting) > 1 else "no candidate can be written on the letters") + \
            "; no owner approval, no rule decides between them, and how often a reading occurs is not evidence"
    return families


def families_of(blocks: dict[str, list[tuple[Word, str]]], names: frozenset[str]) -> list[Family]:
    """Every word family the film reads in more than one way, with every occurrence of each written form."""
    found = CS.variants(blocks, names)
    out = {v.key: Family(v.key, v.kind, {r: list(b) for r, b in v.readings.items()}) for v in found if not v.legitimate}
    out |= {v.key: Family(v.key, v.kind, {r: list(b) for r, b in v.readings.items()}, status="retained_variant", rule="homograph",
                          evidence=v.legitimate) for v in found if v.legitimate}
    for block_id, words in blocks.items():
        for word, reading in words:
            for key in (word.key, key_of(word.plain.split("‌")[0])):
                if key in out:
                    out[key].forms.setdefault(word.key, []).append((block_id, word.surface, reading))
                    break
    return list(out.values())


def readings_for(family: Family) -> dict[str, str]:
    """The reading every written form of a resolved family is given: the stem's own form takes the resolved reading, an inflected form takes it with
    its suffix as its occurrences say it."""
    out: dict[str, str] = {}
    for key, occurrences in family.forms.items():
        if key == family.key:
            out[key] = family.reading
            continue
        for _, surface, reading in occurrences:
            cut = CS.stem_of(tokenize(surface)[0], reading)
            said = CS.normal(reading)
            if cut is not None and said.startswith(cut[1]):
                out[key] = family.reading + said[len(cut[1]):]
                break
    return out


def save(db: Session, case_id: int, language: str, families: list[Family]) -> int:
    """Keeps the resolved and retained families of a case (a family the owner approved is the owner's approval, not kept here). Returns how many rows."""
    rows = 0
    for f in families:
        if f.status == "unresolved" or f.rule == "owner":      # the owner's approval is an approval, not a resolution
            continue
        targets = readings_for(f) if f.status == "resolved" else {f.key: ""}
        for key, reading in targets.items():
            row = db.query(ReadingResolution).filter_by(language=language, case_id=case_id, surface=key).one_or_none()
            if row is None:
                row = ReadingResolution(language=language, case_id=case_id, surface=key)
                db.add(row)
            row.kind, row.reading, row.status, row.rule, row.evidence = f.kind, reading, f.status, f.rule, f.evidence
            row.candidates = json.dumps({r: b for r, b in f.candidates.items()}, ensure_ascii=False)
            rows += 1
    db.commit()
    return rows


def resolved_entries(db: Session, case_id: int, language: str) -> list[dict]:
    """The case's resolved readings as film table approvals of kind "resolved" (a retained variant sets no reading)."""
    return [{"surface": r.surface, "latin": r.reading, "kind": "resolved_name" if r.kind == "name" else "resolved"} for r in
            db.query(ReadingResolution).filter_by(case_id=case_id, language=language, status="resolved") if r.reading]


def decide(db: Session, case_id: int, language: str, family: Family, reading: str, reviewer: str) -> int:
    """The owner's decision for a family: kept as a resolution of the case (not as an approval, which would respell the word in every film)."""
    if reading not in family.candidates:
        raise ValueError(f"{reading} is none of the film's readings of {family.key} ({', '.join(family.candidates)})")
    family.status, family.rule, family.reading = "resolved", "owner_decision", reading
    family.evidence = f"the owner ({reviewer}) decided"
    return save(db, case_id, language, [family])


def lexicon_note(db: Session, language: str, key: str) -> str:
    """What the pronunciation lexicon (kept by the listening review model, not by a person) holds for a word: weak evidence, shown, never applied."""
    row = db.query(PronunciationLexicon).filter_by(language=language, surface=key).first()
    return f"lexicon (listening review model, not a person): {row.reading}" if row else ""


ENGLISH_SOUNDS = (("ph", "f"), ("ck", "k"), ("th", "t"), ("sh", "S"), ("ch", "C"), ("c", "k"), ("q", "k"), ("x", "ks"), ("w", "v"))
PERSIAN_SOUNDS = {"sh": "S", "ch": "C", "kh": "k", "gh": "g", "zh": "j"}


def skeleton(english: str) -> str:
    """The consonants of an English spelling, roughly as a Persian transliteration keeps them (the evidence that a name renders that English word)."""
    word = english.lower()
    for a, b in ENGLISH_SOUNDS:
        word = word.replace(a, b)
    return re.sub(r"(.)\1", r"\1", re.sub(r"[aeiouyh]", "", word))


def sources_of(english: str, families: list[Family]) -> dict[str, str]:
    """A name family's source spelling: the one capitalised word of the English story whose consonants are the name's (Caleb for کالب: k-l-b);
    none when no word or more than one fits."""
    words = set(re.findall(r"\b[A-Z][a-z]+\b", english))
    out: dict[str, str] = {}
    for f in families:
        if f.kind != "name":
            continue
        consonants = {re.sub(r"(.)\1", r"\1", "".join(PERSIAN_SOUNDS.get(p, p) for p in REF.phonemes_of(r) if p in REF.CONSONANTS and p not in ("'", "h")))
                      for r in f.candidates}
        fits = sorted(w for w in words if skeleton(w) in consonants)
        if len(fits) == 1:
            out[f.key] = fits[0]
    return out


def analyse(db: Session, loaded, language: str) -> list[Family]:
    """Every family of a loaded version (pronunciation_explain.read) resolved by the rules: the families the film reads in more than one way now, and the
    names whose stored readings disagreed before the film table made them look consistent."""
    families = families_of(loaded.current, loaded.voicing.names)
    known = {f.key for f in families}
    for f in families_of(loaded.stored, loaded.voicing.names):
        if f.kind == "name" and f.key not in known and f.status != "retained_variant":
            families.append(f)
    return resolve(families, loaded.voicing.fixed, sources_of(loaded.english, families))


def contexts(loaded, family: Family, limit: int = 2) -> dict[str, list[dict]]:
    """For each candidate reading of a family, where the film reads it so: block and sentence."""
    out: dict[str, list[dict]] = {r: [] for r in family.candidates}
    for block_id, pairs in loaded.stored.items():
        sentence = " ".join(w.surface + w.after for w, _ in pairs)
        for word, said in pairs:
            if word.key not in family.forms:
                continue
            cut = CS.stem_of(word, said)
            stem = cut[1] if cut else CS.normal(said)
            if stem in out and len(out[stem]) < limit and all(c["block"] != block_id for c in out[stem]):
                out[stem].append({"block": block_id, "sentence": sentence})
    return out


def review(db: Session, loaded, language: str, families: list[Family]) -> list[dict]:
    """The owner-review entries: only the families the evidence cannot decide, each with its candidates, their evidence and contexts."""
    out = []
    for f in families:
        if f.status != "unresolved":
            continue
        where = contexts(loaded, f)
        out.append({**f.describe(), "lexicon": lexicon_note(db, language, f.key),
                    "evidence_per_candidate": {r: {"written_on_the_letters": letters_fit(f.key, r), "blocks": len(b), "contexts": where.get(r, [])}
                                               for r, b in f.candidates.items()}})
    return out


def stored(db: Session, case_id: int, language: str) -> list[ReadingResolution]:
    """The resolutions kept for a case (the words they settled no longer conflict, so they are shown from here)."""
    return db.query(ReadingResolution).filter_by(case_id=case_id, language=language).order_by(ReadingResolution.surface).all()


def stored_family(row: ReadingResolution) -> Family:
    candidates = json.loads(row.candidates or "{}")
    return Family(row.surface, row.kind, candidates, {row.surface: []}, row.status, row.rule, row.reading, row.evidence)
