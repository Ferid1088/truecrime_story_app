"""The pronunciation memory: what a person approved as the pronunciation of a word in its context, what the agents expect, how the voice was made to say
it and how often that worked, what was observed in generated audio, and every change with its author.

Authority: a person's approval is never overwritten by an agent (an agent's different proposal is recorded as blocked and the approval stays); a second
person's different approval needs an explicit `supersede`; every state is kept and can be rolled back by a person. An approved PRONUNCIATION (what is
right) is kept apart from a validated INTERVENTION (what made the voice say it): the second is only evidence that the voice obeyed it, for one exact
written form, voice and model. An exact-form intervention is never moved onto a form with other vowel marks."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import (
    PronunciationAuditLog,
    PronunciationEntry,
    PronunciationEntryVersion,
    PronunciationEvidence,
    PronunciationIntervention,
)
from app.documentary.irab.rules import SPOKEN, canonical
from app.documentary.reading.words import key_of
from app.utils import utc_now

HUMAN, AGENT = "human:", "agent:"
SNAPSHOT_FIELDS = ("language", "surface", "surface_key", "prev_word", "next_word", "sense", "voice_id", "model_id", "scope", "sentence", "grammar",
                   "expected_irab", "observed_wrong", "approved_pronunciation", "approved_ipa", "status", "source", "confidence", "reviewer", "case_id")
KINDS = ("dictionary_alias", "dictionary_phoneme", "irab", "alt_spelling", "rewrite")
EVIDENCE_KINDS = ("acoustic", "asr", "model", "human", "inference")


class Contradiction(ValueError):
    """A person's approval differs from the one already kept; it needs an explicit `supersede`."""

    def __init__(self, entry: PronunciationEntry, proposed: str):
        super().__init__(f"entry {entry.id} ({entry.surface}) is approved as {entry.approved_pronunciation!r}, not {proposed!r}")
        self.entry, self.proposed = entry, proposed


@dataclass
class Proposal:
    entry: PronunciationEntry
    created: bool
    blocked_by: PronunciationEntry | None = None          # the person's approval that the proposal would have changed


@dataclass
class Match:
    entry: PronunciationEntry
    specificity: int
    reason: str


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text.strip())


class PronunciationMemory:
    def __init__(self, db: Session):
        self.db = db

    # --- writing -----------------------------------------------------------------------------------------------------------------------

    def snapshot(self, entry: PronunciationEntry) -> str:
        return json.dumps({f: getattr(entry, f) for f in SNAPSHOT_FIELDS}, ensure_ascii=False, sort_keys=True)

    def record(self, entry: PronunciationEntry, actor: str, action: str, before: str, reason: str = "") -> None:
        """Keeps the new state as a version and writes the audit line (the caller commits)."""
        after = self.snapshot(entry)
        self.db.add(PronunciationEntryVersion(entry_id=entry.id, version=entry.version, snapshot=after, actor=actor, reason=reason))
        self.db.add(PronunciationAuditLog(entry_id=entry.id, actor=actor, action=action, before=before, after=after, note=reason))

    def find_exact(self, language: str, surface: str, prev_word: str, next_word: str, sense: str, voice_id: str, model_id: str) -> PronunciationEntry | None:
        return (self.db.query(PronunciationEntry).filter_by(language=language, surface=surface, prev_word=prev_word, next_word=next_word, sense=sense,
                                                            voice_id=voice_id, model_id=model_id).first())

    def propose(self, *, language: str, surface: str, expected_irab: str, actor: str, prev_word: str = "", next_word: str = "", sense: str = "",
                voice_id: str = "", model_id: str = "", scope: str = "context", sentence: str = "", grammar: dict | None = None, observed_wrong: str = "",
                confidence: float = 0.0, case_id: int | None = None) -> Proposal:
        """What an agent expects for a word. A person's approval for the same word in the same context is left as it is."""
        surface = nfc(surface)
        entry = self.find_exact(language, surface, prev_word, next_word, sense, voice_id, model_id)
        if entry is not None and entry.status == "human_approved":
            if canonical(entry.approved_pronunciation or "") != canonical(expected_irab):
                self.db.add(PronunciationAuditLog(entry_id=entry.id, actor=actor, action="proposal_blocked", before=self.snapshot(entry),
                                                  after=json.dumps({"expected_irab": expected_irab, "observed_wrong": observed_wrong}, ensure_ascii=False),
                                                  note="a person's approval is not changed by an agent"))
                self.db.commit()
                return Proposal(entry, False, blocked_by=entry)
            return Proposal(entry, False)
        before = self.snapshot(entry) if entry is not None else ""
        created = entry is None
        if created:
            entry = PronunciationEntry(language=language, surface=surface, surface_key=key_of(surface), prev_word=prev_word, next_word=next_word, sense=sense,
                                       voice_id=voice_id, model_id=model_id, scope=scope, status="candidate", source="agent", version=1)
            self.db.add(entry)
        else:
            entry.version += 1
        entry.sentence, entry.grammar = sentence or entry.sentence, json.dumps(grammar or {}, ensure_ascii=False)
        entry.expected_irab, entry.observed_wrong, entry.confidence, entry.case_id = expected_irab, observed_wrong or entry.observed_wrong, confidence, case_id
        self.db.flush()
        self.record(entry, actor, "propose" if created else "update", before)
        self.db.commit()
        return Proposal(entry, created)

    def approve(self, entry_id: int, *, pronunciation: str, reviewer: str, scope: str | None = None, ipa: str | None = None, supersede: bool = False,
                reason: str = "") -> PronunciationEntry:
        """A person approves how the word is pronounced. Only a named reviewer can; a different earlier approval needs `supersede`."""
        if not reviewer.strip():
            raise PermissionError("an approval needs the name of the person who gives it")
        pronunciation = nfc(pronunciation)
        if not pronunciation or not SPOKEN.match(pronunciation):
            raise ValueError("the approved pronunciation must be Persian letters and marks")
        entry = self.db.get(PronunciationEntry, entry_id)
        if entry.status == "human_approved" and canonical(entry.approved_pronunciation or "") != canonical(pronunciation) and not supersede:
            raise Contradiction(entry, pronunciation)
        before = self.snapshot(entry)
        entry.approved_pronunciation, entry.approved_ipa = pronunciation, ipa or entry.approved_ipa
        entry.status, entry.source, entry.reviewer, entry.approved_at = "human_approved", "human", reviewer, utc_now()
        entry.scope = scope or entry.scope
        entry.version += 1
        self.db.flush()
        self.record(entry, HUMAN + reviewer, "approve", before, reason)
        self.db.commit()
        return entry

    def reject(self, entry_id: int, *, reviewer: str, reason: str = "") -> PronunciationEntry:
        if not reviewer.strip():
            raise PermissionError("a rejection needs the name of the person who gives it")
        entry = self.db.get(PronunciationEntry, entry_id)
        before = self.snapshot(entry)
        entry.status, entry.reviewer, entry.version = "rejected", reviewer, entry.version + 1
        self.db.flush()
        self.record(entry, HUMAN + reviewer, "reject", before, reason)
        self.db.commit()
        return entry

    def rollback(self, entry_id: int, version: int, *, reviewer: str) -> PronunciationEntry:
        """A person puts an entry back to an earlier state (as a new version: the history keeps everything)."""
        if not reviewer.strip():
            raise PermissionError("a rollback needs the name of the person who gives it")
        old = self.db.query(PronunciationEntryVersion).filter_by(entry_id=entry_id, version=version).one()
        entry = self.db.get(PronunciationEntry, entry_id)
        before = self.snapshot(entry)
        for field, value in json.loads(old.snapshot).items():
            setattr(entry, field, value)
        entry.version += 1
        self.db.flush()
        self.record(entry, HUMAN + reviewer, "rollback", before, f"back to version {version}")
        self.db.commit()
        return entry

    def add_intervention(self, entry_id: int, *, kind: str, exact_form: str, replacement: str, voice_id: str = "", model_id: str = "",
                         dictionary_id: str | None = None, dictionary_version_id: str | None = None) -> PronunciationIntervention:
        if kind not in KINDS:
            raise ValueError(f"unknown intervention {kind!r}")
        exact_form = nfc(exact_form)
        found = (self.db.query(PronunciationIntervention).filter_by(entry_id=entry_id, kind=kind, exact_form=exact_form, replacement=replacement,
                                                                    voice_id=voice_id, model_id=model_id).first())
        if found is not None:
            return found
        item = PronunciationIntervention(entry_id=entry_id, kind=kind, exact_form=exact_form, replacement=replacement, voice_id=voice_id, model_id=model_id,
                                         dictionary_id=dictionary_id, dictionary_version_id=dictionary_version_id)
        self.db.add(item)
        self.db.commit()
        return item

    def record_outcome(self, intervention_id: int, *, success: bool, audio_ref: str = "") -> PronunciationIntervention:
        """The voice obeyed the intervention or did not (judged by evidence, not by how the rule reads). Validated: obeyed and never failed."""
        item = self.db.get(PronunciationIntervention, intervention_id)
        if success:
            item.successes += 1
        else:
            item.failures += 1
        item.last_audio_ref = audio_ref or item.last_audio_ref
        if item.successes and not item.failures:
            item.status, item.validated_at = "validated", item.validated_at or utc_now()
        elif item.failures >= 2 and not item.successes:
            item.status = "failed"
        else:
            item.status = "candidate"
        self.db.add(PronunciationAuditLog(entry_id=item.entry_id, actor=AGENT + "outcome", action="outcome", before="", note=json.dumps(
            {"intervention": item.id, "success": success, "audio": audio_ref}, ensure_ascii=False), after=item.status))
        self.db.commit()
        return item

    def add_evidence(self, *, surface: str, kind: str, verdict: str, entry_id: int | None = None, **fields) -> PronunciationEvidence:
        """What was observed. `inference` and `model` are predictions; only `acoustic` (and `human`) evidence can support a mismatch as confirmed."""
        if kind not in EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence kind {kind!r}")
        row = PronunciationEvidence(entry_id=entry_id, surface=nfc(surface), kind=kind, verdict=verdict, **fields)
        self.db.add(row)
        self.db.commit()
        return row

    # --- reading -----------------------------------------------------------------------------------------------------------------------

    def find(self, language: str, surface: str, *, prev_word: str = "", next_word: str = "", sense: str = "", voice_id: str = "", model_id: str = "") -> list[Match]:
        """The entries that apply to this exact written form here, the most specific first, a person's approval before a candidate. Another written form
        of the same word (other vowel marks) never applies."""
        surface = nfc(surface)
        out: list[Match] = []
        for e in self.db.query(PronunciationEntry).filter_by(language=language, surface=surface).filter(PronunciationEntry.status != "rejected"):
            fits = all(not want or want == have for want, have in ((e.prev_word, prev_word), (e.next_word, next_word), (e.sense, sense),
                                                                    (e.voice_id, voice_id), (e.model_id, model_id)))
            if fits:
                specific = sum(1 for want in (e.prev_word, e.next_word, e.sense, e.voice_id, e.model_id) if want)
                out.append(Match(e, specific, "exact written form"))
        return sorted(out, key=lambda m: (m.entry.status != "human_approved", -m.specificity, m.entry.id))

    def reusable(self, language: str, surface: str, **context) -> list[PronunciationIntervention]:
        """The validated interventions for a word a person approved, for this exact written form and this voice and model."""
        voice_id, model_id = context.get("voice_id", ""), context.get("model_id", "")
        out: list[PronunciationIntervention] = []
        for m in self.find(language, surface, **context):
            if m.entry.status != "human_approved":
                continue
            for i in self.db.query(PronunciationIntervention).filter_by(entry_id=m.entry.id, status="validated"):
                if i.exact_form == nfc(surface) and i.voice_id in ("", voice_id) and i.model_id in ("", model_id):
                    out.append(i)
        return out

    def conflicts(self, language: str) -> list[tuple[PronunciationEntry, PronunciationEntry]]:
        """Approvals that contradict each other: the same word in the same context, written with different marks, approved as different pronunciations."""
        rows = self.db.query(PronunciationEntry).filter_by(language=language, status="human_approved").order_by(PronunciationEntry.id).all()
        out = []
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                same_place = (a.surface_key, a.prev_word, a.next_word, a.sense, a.voice_id, a.model_id) == (b.surface_key, b.prev_word, b.next_word, b.sense, b.voice_id, b.model_id)
                if same_place and a.surface != b.surface and canonical(a.approved_pronunciation or "") != canonical(b.approved_pronunciation or ""):
                    out.append((a, b))
        return out

    def history(self, entry_id: int) -> list[PronunciationEntryVersion]:
        return self.db.query(PronunciationEntryVersion).filter_by(entry_id=entry_id).order_by(PronunciationEntryVersion.version).all()
