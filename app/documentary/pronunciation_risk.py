"""The pronunciation risk engine: for every word of a block, in its sentence, how likely it is that the voice says it wrong, and why.

The risk is the sum of the weights (config pronunciation_risk) of the signals found for the word. Signals come from what is known before the voice
(the reading stage's doubts and confidence, the kind of word, the letters that have tripped the voice, the pronunciation memory) and from what was
observed after earlier voices (failed interventions, a listening model that heard it wrong, a person's or an acoustic mismatch, a person's labelled
clip). A score under `medium_from` is LOW: that is the absence of a reason to worry, never the proof of a right pronunciation, and the engine says so
in `verification` (nothing but an approved memory entry is more than "unverified")."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    PhoneticSample,
    PronunciationEntry,
    PronunciationEvidence,
    PronunciationIntervention,
)
from app.documentary import phonetic_benchmark
from app.documentary.pronunciation_memory import APPROVED
from app.documentary.reading import scheme
from app.documentary.reading.consistency import family
from app.documentary.reading.ezafe import Boundary, EzafeAnalyzer
from app.documentary.reading.reference import Reference
from app.documentary.reading.voicing import WordSignal
from app.documentary.reading.words import Word, key_of

LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


@dataclass(frozen=True)
class Signal:
    name: str
    weight: int
    evidence: str


@dataclass
class WordRisk:
    n: int
    surface: str
    reading: str
    score: int
    level: str
    signals: list[Signal] = field(default_factory=list)
    verification: str = "unverified"          # human_approved | auto_verified | unverified: a LOW word is "unverified" too

    @property
    def rationale(self) -> str:
        return "; ".join(s.evidence for s in self.signals if s.weight > 0)

    def describe(self) -> dict:
        return {"n": self.n, "surface": self.surface, "reading": self.reading, "score": self.score, "level": self.level, "verification": self.verification,
                "signals": [{"name": s.name, "weight": s.weight, "evidence": s.evidence} for s in self.signals]}


class History:
    """Everything the memory and the earlier voices say about the words of a language for one voice and model, read once."""

    def __init__(self, db: Session, language: str, voice_id: str, model_id: str):
        def mine(voice: str, model: str) -> bool:
            return voice in ("", voice_id) and model in ("", model_id)

        self.entries: dict[str, list[PronunciationEntry]] = {}
        for e in db.query(PronunciationEntry).filter(PronunciationEntry.language == language, PronunciationEntry.status != "rejected"):
            if mine(e.voice_id, e.model_id):
                self.entries.setdefault(e.surface_key, []).append(e)
        self.failed: dict[str, list[PronunciationIntervention]] = {}
        for i, e in db.query(PronunciationIntervention, PronunciationEntry).join(PronunciationEntry, PronunciationEntry.id == PronunciationIntervention.entry_id).filter(
                PronunciationEntry.language == language, PronunciationIntervention.failures > 0):
            if mine(i.voice_id, i.model_id):
                self.failed.setdefault(e.surface_key, []).append(i)
        self.evidence: dict[str, list[PronunciationEvidence]] = {}
        for ev in db.query(PronunciationEvidence).join(PronunciationEntry, PronunciationEntry.id == PronunciationEvidence.entry_id).filter(PronunciationEntry.language == language):
            if mine(ev.voice_id, ev.model_id):
                self.evidence.setdefault(key_of(ev.surface), []).append(ev)
        self.labelled: dict[str, list[PhoneticSample]] = {}
        for s in db.query(PhoneticSample).filter_by(language=language, status="human_verified"):
            if mine(s.voice_id, s.model_id):
                self.labelled.setdefault(key_of(s.surface), []).append(s)


class RiskEngine:
    def __init__(self, db: Session, language: str, voice_id: str, model_id: str, names: frozenset[str] = frozenset(),
                 variants: dict[str, tuple[str, ...]] | None = None, unresolved: dict[str, str] | None = None):
        self.cfg, self.names, self.variants, self.unresolved = ai_config.pronunciation_risk, names, variants or {}, unresolved or {}
        self.homographs = {key_of(w): readings for w, readings in ai_config.homographs.words.items()}
        self.history = History(db, language, voice_id, model_id)

    def weight(self, name: str) -> int:
        return self.cfg.weights[name]

    def level_of(self, score: int) -> str:
        c = self.cfg
        return "CRITICAL" if score >= c.critical_from else "HIGH" if score >= c.high_from else "MEDIUM" if score >= c.medium_from else "LOW"

    def signals_of(self, word: Word, reading: str, signal: WordSignal, boundary: Boundary | None = None, reference: Reference | None = None) -> list[Signal]:
        found: list[Signal] = []

        def add(name: str, evidence: str, weight: int | None = None) -> None:
            found.append(Signal(name, self.weight(name) if weight is None else weight, evidence))

        stem, _ = scheme.split_ezafe(scheme.split_pause(reading)[0])
        if signal.doubted:
            add("reading_doubt", "the checker and the arbiter left a doubt about this word's reading")
        if signal.confidence == "low":
            add("low_confidence", "the reading editor was not sure of this reading")
        elif signal.confidence == "medium":
            add("medium_confidence", "the reading editor was only moderately sure")
        if signal.overridden:
            add("code_override", "the editor's reading was replaced by the film table's: the two disagreed")
        if word.key in self.names:
            add("name_or_foreign", "a person, a place or a foreign word of this film")
        if phonetic_benchmark.category_of(word.surface, stem, set()) == "colloquial":
            add("colloquial", "a colloquial form (spoken ending)")
        found += self.ezafe_signals(boundary)
        readings_of = self.homographs.get(word.key)
        if readings_of and word.surface == word.plain:
            add("homograph", f"{word.plain} is written the same for {' / '.join(readings_of)}: the voice cannot tell from the letters which one is meant")
        if known := family(word, self.variants):
            add("reading_inconsistent", f"the film reads {known} in more than one way ({' / '.join(self.variants[known])}): one of them is wrong")
        elif known := family(word, self.unresolved):
            add("reading_inconsistent", self.unresolved[known])
        if reference is not None and reference.irab is None:
            add("reference_unaligned", "the reading cannot be written on the word's own letters: the reading or the spelling is not what the other expects")
        letters = [ch for ch in self.cfg.hazard_letters if ch in word.plain]
        if letters and word.surface == word.plain:
            add("hazard_letter", f"unmarked letters that have tripped the voice: {' '.join(letters)}")
        if len(stem) > 2 and stem.endswith("h") and stem[-2] in "aeoāiu":
            add("final_consonant_h", "a consonant h ends the word: the voice reads a final ه as a vowel")
        found += self.remembered(word, reading)
        return found

    def ezafe_signals(self, b: Boundary | None) -> list[Signal]:
        """Only what makes the ezafe after the word a risk for the voice: it is not written (the voice has to infer it), the evidence cannot settle it, the
        sources disagree, or the reading writes a form its own final sound does not take. A settled, written ezafe is no risk, and neither is none."""
        if b is None:
            return []
        found: list[Signal] = []
        if b.ezafe_type == "UNCERTAIN":
            found.append(Signal("ezafe_uncertain", self.weight("ezafe_uncertain"), f"whether an ezafe joins {b.word} and {b.next_word} is not settled ({b.grammatical_relation})"))
        if b.evidence_type == "conflict":
            found.append(Signal("ezafe_conflict", self.weight("ezafe_conflict"), f"the ezafe after {b.word}: {', '.join(b.conflicts)} disagree"))
        if b.phonology_error:
            found.append(Signal("ezafe_phonology", self.weight("ezafe_phonology"), b.phonology_error))
        if b.ezafe_required and not b.written:
            found.append(Signal("ezafe_unwritten", self.weight("ezafe_unwritten"), f"an ezafe -{b.ezafe_type.lower()} ({b.grammatical_relation}) that the text does not write"))
        return found

    @staticmethod
    def failed_before(e: PronunciationEntry) -> bool:
        """Whether an entry records that the voice got the word wrong: one an earlier system learned or a person made, or one with a wrong form observed.
        The planner's own candidates (an untried change) are not failures: counting them would make every word it once touched riskier by itself."""
        return e.source != "agent" or bool(e.observed_wrong)

    def remembered(self, word: Word, reading: str) -> list[Signal]:
        found: list[Signal] = []
        h = self.history
        for e in h.entries.get(word.key, []):
            if e.status not in APPROVED and self.failed_before(e):
                found.append(Signal("earlier_failure", self.weight("earlier_failure"), f"an earlier system learned a correction for this word (entry {e.id}, {e.status}, unverified)"))
                break
        else:
            if any(e.surface == word.surface and e.status in APPROVED for e in h.entries.get(word.key, [])):
                found.append(Signal("earlier_failure", self.weight("earlier_failure"), "this word is in the memory as an approved correction: it failed before"))
        for i in h.failed.get(word.key, []):
            found.append(Signal("intervention_failed", self.weight("intervention_failed"), f"intervention {i.id} ({i.kind}) failed {i.failures} time(s) with this voice"))
            break
        for ev in h.evidence.get(word.key, []):
            if ev.verdict == "mismatch" and ev.kind in ("acoustic", "human"):
                found.append(Signal("acoustic_mismatch", self.weight("acoustic_mismatch"), f"{ev.kind} evidence {ev.id}: the voice said it differently"))
                break
        for ev in h.evidence.get(word.key, []):
            if ev.verdict == "mismatch" and ev.kind == "model":
                found.append(Signal("heard_wrong_by_listener", self.weight("heard_wrong_by_listener"), f"a listening model heard it wrong (evidence {ev.id})"))
                break
        for s in h.labelled.get(word.key, []):
            if s.human_key and s.human_key != s.expected_key:
                found.append(Signal("acoustic_mismatch", self.weight("acoustic_mismatch"), f"a person who listened labelled clip {s.id} as {s.human_key}, not {s.expected_key}"))
                break
        else:
            if any(s.human_key == s.expected_key for s in h.labelled.get(word.key, [])):
                found.append(Signal("acoustic_match", self.weight("acoustic_match"), "a person who listened confirmed this word in this voice"))
        return found

    def verification_of(self, word: Word) -> str:
        approved = [e for e in self.history.entries.get(word.key, []) if e.status in APPROVED and e.surface == word.surface]
        return "human_approved" if any(e.status == "human_approved" for e in approved) else "auto_verified" if approved else "unverified"

    def assess(self, words: list[Word], readings: list[str], signals: list[WordSignal] | None = None, boundaries: list[Boundary] | None = None,
               references: list[Reference | None] | None = None) -> list[WordRisk]:
        """The risk of each word of a block (its readings, in order). A number is read by code and is LOW. `boundaries`: the ezafe analysis of the block
        (made here from the readings when not given)."""
        signals = signals or [WordSignal()] * len(words)
        if boundaries is None:
            boundaries = EzafeAnalyzer().analyze(words, readings, confidences=[s.confidence for s in signals], table_set=frozenset(n for n, s in enumerate(signals) if s.overridden))
        out: list[WordRisk] = []
        for word, reading in zip(words, readings, strict=True):
            if word.number or reading == "NUM":
                out.append(WordRisk(word.n, word.surface, reading, 0, "LOW", [Signal("number", 0, "read by code")]))
                continue
            found = self.signals_of(word, reading, signals[word.n], boundaries[word.n], references[word.n] if references else None)
            score = sum(s.weight for s in found)
            out.append(WordRisk(word.n, word.surface, reading, score, self.level_of(score), found, self.verification_of(word)))
        return out
