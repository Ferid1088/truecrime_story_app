"""The adaptive TTS intervention planner: for every word of a block the smallest change to what the voice is given that its risk and the memory justify,
and nothing for the words that need none. The block's text is the original text; only the words with a decision other than `keep` differ.

Strategies, in the order they are tried for a word:
  respelling       a pronunciation a person (or the app) approved, or a validated respelling, for this exact written form in this context;
  dictionary_rule  a validated dictionary alias for it (only when the language's voice has a dictionary);
  ezafe_mark       only the ezafe written on the word (a kasra, a hamza on ه, a carrier yeh), for a HIGH or CRITICAL word whose risk is its unwritten
                   ezafe alone and whose ezafe the analyzer settled (reading/ezafe.py; a candidate);
  selective_marks  the word's own letters with their short vowels as marks, only for a HIGH or CRITICAL word whose reading can be written on its own
                   letters (a candidate: nothing says yet that the voice obeys it);
  number_words     a number in digits, given in the words its context calls for (reading/numbers.py; read by code, outside the change budget);
  escalate         no safe change, or an ezafe the evidence does not settle: the word stays as written and is reported for a person;
  keep             the rest. LOW is no proof, MEDIUM is left to the listening QA.
A candidate never becomes an approval here. How many unvalidated changes a block may carry is limited (config), so that the speech stays natural.
For a PPIE language (config ppie) the planner remembers nothing: no memory entry, intervention or evidence of an earlier run is read, and none of
this run's decisions is written."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import PronunciationEntry, PronunciationIntervention
from app.documentary.pronunciation_memory import APPROVED, PronunciationMemory
from app.documentary.pronunciation_risk import RiskEngine, WordRisk
from app.documentary.reading import consistency as CS
from app.documentary.reading import ezafe as EZ
from app.documentary.reading import numbers as NUM
from app.documentary.reading import reference as REF
from app.documentary.reading import scheme, spelling
from app.documentary.reading.voicing import Voicing
from app.documentary.reading.words import VOWEL_MARKS, Word, key_of, substitute, tokenize

CHANGES = ("respelling", "dictionary_rule", "ezafe_mark", "selective_marks")
TEXT_CHANGES = CHANGES + ("number_words",)            # every decision that changes what the voice is given (numbers are outside the change budget)
EZAFE_SIGNALS = ("ezafe_unwritten", "ezafe_uncertain", "ezafe_conflict", "ezafe_phonology")
KIND_STRATEGY = {"dictionary_alias": "dictionary_rule", "dictionary_phoneme": "dictionary_rule", "irab": "respelling", "alt_spelling": "respelling", "rewrite": "respelling"}
JOINERS = re.compile("[‌‍]")


@dataclass
class Decision:
    n: int
    surface: str
    level: str
    strategy: str
    basis: str
    replacement: str | None = None
    validated: bool = False                 # a person or an earlier validated use stands behind the replacement
    needs_review: bool = False
    intervention_id: int | None = None
    reference: REF.Reference | None = None  # the complete phoneme-first reference of the word in its sentence (never the voice's input by itself)
    candidates: dict[str, str] = field(default_factory=dict)       # the forms of the reference the voice could be given (minimal, complete)
    reference_status: str = ""              # VALID | UNREPRESENTABLE_BY_IRAB | READING_UNRESOLVED | NOT_APPLICABLE (reference.diagnose)
    reference_cause: str = ""

    def describe(self) -> dict:
        return {"n": self.n, "surface": self.surface, "level": self.level, "strategy": self.strategy, "replacement": self.replacement, "basis": self.basis,
                "validated": self.validated, "needs_review": self.needs_review, "intervention_id": self.intervention_id, "candidates": self.candidates,
                "reference_status": self.reference_status, "reference_cause": self.reference_cause,
                **({"reference": self.reference.describe()} if self.reference is not None and (self.strategy != "keep" or self.level in ("HIGH", "CRITICAL")) else {})}


@dataclass
class BlockPlan:
    block_id: str
    text: str
    rules: list[dict] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    risks: list[WordRisk] = field(default_factory=list)
    note: str = ""
    boundaries: list[EZ.Boundary] = field(default_factory=list)       # the ezafe analysis of the block, one per word

    @property
    def naturalness(self) -> dict:
        return naturalness(self.decisions)

    def describe(self) -> dict:
        return {"block_id": self.block_id, "note": self.note, "naturalness": self.naturalness, "rules": self.rules,
                "words": [{"risk": r.describe(), "decision": d.describe(), **({"ezafe": b.describe()} if b.ezafe_type != "NONE" else {})}
                          for r, d, b in zip(self.risks, self.decisions, self.boundaries or [None] * len(self.decisions), strict=True)]}


def naturalness(decisions: list[Decision]) -> dict:
    """How much of the block was changed, measured on the decisions alone (not on the audio: how natural the voice sounds is for the listening QA)."""
    changed = [d.strategy in CHANGES for d in decisions]
    run = longest = 0
    for c in changed:
        run = run + 1 if c else 0
        longest = max(longest, run)
    return {"words": len(decisions), "changed": sum(changed), "changed_fraction": round(sum(changed) / len(decisions), 3) if decisions else 0.0,
            "longest_run": longest, "unvalidated_changed": sum(1 for d, c in zip(decisions, changed, strict=True) if c and not d.validated)}


def settled_reading(reading: str, b: EZ.Boundary) -> str:
    """The reading with the ezafe the analyzer settled (an uncertain one stays as the agents read it): the reading the reference is built from."""
    stem, ezafe = scheme.split_ezafe(scheme.split_pause(reading)[0])
    if b.own_vowel:
        return stem + "e"                                       # the word's own written -e (a copula), not an ezafe
    if b.stem and not ezafe and b.stem != stem:
        stem, ezafe = b.stem, "e"                               # the analyzer read an ezafe written without its hyphen (bedune = bedun-e)
    if b.written and not ezafe and stem[-3:-2] in scheme.VOWELS and stem.endswith("ye"):
        stem = stem[:-2]                                       # an ezafe the reading wrote without its hyphen (edāreye): not a second one
    return scheme.ezafe_form(stem) if b.ezafe_type in ("E", "YE") and stem else stem if b.ezafe_type == "NONE" else scheme.split_pause(reading)[0]


def settled_words(words: list[Word], readings: list[str], signals: list) -> list[tuple[Word, str]]:
    """The block's words with their readings as the ezafe analyzer settles them: what is compared across blocks (an ezafe a reading wrote without its
    hyphen is an ezafe, not another reading of the word)."""
    bounds = EZ.EzafeAnalyzer().analyze(words, readings, None, [s.confidence for s in signals], frozenset(n for n, s in enumerate(signals) if s.overridden))
    return [(w, settled_reading(r, b)) for w, r, b in zip(words, readings, bounds, strict=True)]


def ezafe_sense(b: EZ.Boundary) -> str:
    """The memory's sense of an ezafe correction: the type it was made for (a correction for -e is never reused where -ye is said)."""
    return f"ezafe:{b.ezafe_type}"


def unresolved(b: EZ.Boundary) -> bool:
    """A disagreement about the ezafe that no arbiter settled in the reading's favour: nothing is written on it."""
    return b.evidence_type == "conflict" and not any(o.source == "arbiter" and o.present == b.ezafe_required for o in b.opinions)


def same_relation(entry_id: int, b: EZ.Boundary | None, db: Session) -> bool:
    """Whether an entry kept for an ezafe was made for the same grammatical relation (an entry without one, or a relation nobody named, fits any)."""
    entry = db.get(PronunciationEntry, entry_id)
    try:
        kept = (json.loads(entry.grammar or "{}").get("ezafe") or {}).get("grammatical_relation", "unknown") if entry else "unknown"
    except ValueError:
        kept = "unknown"
    have = b.grammatical_relation if b is not None else "unknown"
    return "unknown" in (kept, have) or kept == have


def skeleton(text: str) -> str:
    return JOINERS.sub("", key_of(VOWEL_MARKS.sub("", text)))


class Planner:
    def __init__(self, db: Session, language: str, voice_id: str, model_id: str):
        self.db, self.language, self.voice_id, self.model_id = db, language, voice_id, model_id
        self.cfg = ai_config.pronunciation_risk
        self.memory = None if ai_config.ppie.fresh(language) else PronunciationMemory(db)          # a PPIE run starts from nothing
        self.engine: RiskEngine | None = None
        self.plans: dict[str, BlockPlan] = {}

    # --- planning --------------------------------------------------------------------------------------------------------------------

    def plan(self, block_id: str, text: str, voicing: Voicing, allow_dictionary: bool = False, covered: frozenset[str] = frozenset()) -> BlockPlan:
        """`covered`: the plain forms of the words the language's dictionary already has a rule for."""
        words, readings = tokenize(text), voicing.readings.get(block_id)
        if readings is None or len(readings) != len(words):
            return self.keep_all(block_id, text, "no reading of this block, or not the words of its reading: left as written")
        if self.engine is None:
            self.engine = RiskEngine(self.db if self.memory is not None else None, self.language, self.voice_id, self.model_id, voicing.names,
                                     voicing.variants, voicing.unresolved)
        signals = voicing.signals.get(block_id)
        boundaries = EZ.EzafeAnalyzer().analyze(words, readings, voicing.opinions.get(block_id), [s.confidence for s in signals] if signals else None,
                                                frozenset(n for n, s in enumerate(signals or []) if s.overridden))
        references = [REF.reference_of(w, settled_reading(readings[w.n], boundaries[w.n])) for w in words]
        diagnoses = [REF.diagnose(w, settled_reading(readings[w.n], boundaries[w.n]), references[w.n], owner=w.key in voicing.fixed,
                                  doubted=bool(signals and signals[w.n].doubted),
                                  inconsistent=bool(CS.family(w, voicing.variants) or CS.family(w, voicing.unresolved)))
                     for w in words]
        risks = self.engine.assess(words, readings, signals, boundaries, references)
        decisions = [self.decide(w, r, readings[w.n], words, voicing, allow_dictionary, covered, boundaries[w.n], references[w.n]) for w, r in zip(words, risks, strict=True)]
        for d, (status, cause) in zip(decisions, diagnoses, strict=True):
            d.reference_status, d.reference_cause = status, cause
        self.limit(decisions)
        plan = BlockPlan(block_id, substitute(text, {d.n: d.replacement for d in decisions if d.strategy in TEXT_CHANGES and d.strategy != "dictionary_rule" and d.replacement}),
                         [self.rule(d) for d in decisions if d.strategy == "dictionary_rule"], decisions, risks, boundaries=boundaries)
        for b, d in zip(boundaries, decisions, strict=True):
            if b.ezafe_type in ("E", "YE"):
                changed = d.strategy in CHANGES and d.replacement is not None
                boundaries[b.n] = replace(b, requires_tts_intervention=d.strategy == "ezafe_mark" or (d.strategy == "selective_marks" and b.ezafe_required is True),
                                          tts_form=d.replacement if changed else None)
        self.plans[block_id] = plan
        return plan

    def keep_all(self, block_id: str, text: str, note: str) -> BlockPlan:
        plan = BlockPlan(block_id, text, note=note)
        self.plans[block_id] = plan
        return plan

    @staticmethod
    def rule(d: Decision) -> dict:
        return {"string_to_replace": d.surface, "type": "alias", "alias": d.replacement}

    def decide(self, word: Word, risk: WordRisk, reading: str, words: list[Word], voicing: Voicing, allow_dictionary: bool, covered: frozenset[str],
               boundary: EZ.Boundary | None = None, reference: REF.Reference | None = None) -> Decision:
        def made(strategy: str, basis: str, **fields) -> Decision:
            return Decision(word.n, word.surface, risk.level, strategy, basis, reference=reference, **fields)

        if word.number or reading == "NUM" or NUM.has_digit(word):
            said = NUM.read(words, word.n)
            if said.words is None:
                return made("escalate", said.basis, needs_review=True)
            return made("number_words", f"{said.kind}: {said.basis}", replacement=said.words)
        if word.key in voicing.fixed:
            said = settled_reading(reading, boundary) if boundary is not None else reading     # the ezafe this sentence settles (the text's own ی)
            spelled = Voicing.spell(Voicing.reading_of(voicing.fixed[word.key], said, word.surface), word.surface)
            return made("respelling", "the reading a person approved (or the reading loop verified) for this word", replacement=spelled, validated=True)
        prev_key = key_of(words[word.n - 1].surface) if word.n else ""
        next_key = key_of(words[word.n + 1].surface) if word.n + 1 < len(words) else ""
        context = {"prev_word": prev_key, "next_word": next_key, "voice_id": self.voice_id, "model_id": self.model_id}
        settled = boundary is not None and boundary.ezafe_type in ("E", "YE")
        contexts = ([{**context, "sense": ezafe_sense(boundary)}] if settled else []) + [context]
        for ctx in contexts if self.memory is not None else []:
            for i in self.memory.reusable(self.language, word.surface, **ctx):
                if not same_relation(i.entry_id, boundary, self.db):
                    continue
                strategy = KIND_STRATEGY[i.kind]
                if strategy == "dictionary_rule" and not allow_dictionary:
                    strategy = "respelling"                           # the alias is the spelling itself
                return made(strategy, f"validated intervention {i.id} ({i.kind}) for this exact form, voice and model", replacement=i.replacement, validated=True,
                            intervention_id=i.id)
            for m in self.memory.find(self.language, word.surface, **ctx):
                if m.entry.status in APPROVED and m.entry.approved_pronunciation and same_relation(m.entry.id, boundary, self.db):
                    return made("respelling", f"entry {m.entry.id} approved ({m.entry.status}), matching context ({m.reason})", replacement=m.entry.approved_pronunciation, validated=True)
        if risk.level in ("HIGH", "CRITICAL") and word.key in covered:
            return made("keep", f"{risk.level} risk, but the language's dictionary already has a rule for this word: the planner adds nothing on top of it")
        if risk.level in ("LOW", "MEDIUM"):
            return made("keep", "low risk: no reason to change the text" if risk.level == "LOW" else "medium risk: left as written, the listening QA checks it")
        known = CS.family(word, voicing.unresolved) or CS.family(word, voicing.variants)
        if known:
            why = voicing.unresolved.get(known) or f"the film reads {known} in more than one way ({' / '.join(voicing.variants[known])})"
            return made("escalate", f"{risk.level} risk; READING_UNRESOLVED: {why}. No reading is chosen by how often it occurs: a person decides: "
                        f"{risk.rationale}", needs_review=True)
        if boundary is not None and (boundary.ezafe_type == "UNCERTAIN" or unresolved(boundary)):
            return made("escalate", f"{risk.level} risk, and the ezafe after it is not settled ({boundary.evidence_type}): {risk.rationale}", needs_review=True)
        if boundary is not None and self.engine.level_of(sum(s.weight for s in risk.signals if s.name not in EZAFE_SIGNALS)) in ("LOW", "MEDIUM"):
            ezafe_only = EZ.mark(word, boundary)                  # the risk is the ezafe: only the ezafe is written
            if ezafe_only is not None:
                return made("ezafe_mark", f"{risk.level} risk from its ezafe -{boundary.ezafe_type.lower()} ({boundary.grammatical_relation}): {risk.rationale}",
                            replacement=ezafe_only, needs_review=risk.level == "CRITICAL")
        candidates = self.marks(word, reference)
        if not candidates:
            why = "; ".join(reference.problems) if reference is not None and reference.problems else "the reading cannot be written on the word's own letters"
            return made("escalate", f"{risk.level} risk, and no validated reference to write on it ({why}): {risk.rationale}", needs_review=True)
        form, why = self.choose(word, candidates)
        if not REF.form_fits(reference.irab or "", candidates[form]):        # the planner's own output is checked against the reference, not trusted
            return made("escalate", f"{risk.level} risk, and the voice form {candidates[form]} does not fit the reference {reference.irab}", needs_review=True)
        return made("selective_marks", f"{risk.level} risk: {risk.rationale}; voice input: {form} ({why})", replacement=candidates[form],
                    needs_review=risk.level == "CRITICAL", candidates=candidates)

    @staticmethod
    def marks(word: Word, reference: REF.Reference | None) -> dict[str, str]:
        """The forms of the word's validated REFERENCE_IRAB the voice could be given: "minimal" (its short vowels, shaddas and ezafe, no sukun) and
        "complete" (the whole reference). Empty when there is no valid reference, the word already has marks, or the reference needs a letter the text
        does not have: then nothing is invented."""
        if word.surface != word.plain or reference is None or not reference.valid or reference.added_carrier or not reference.irab:
            return {}
        forms = {"minimal": REF.minimal(reference.irab), "complete": reference.irab}
        return {k: f for k, f in forms.items() if skeleton(f) == skeleton(word.surface) and f != word.surface}

    def choose(self, word: Word, candidates: dict[str, str]) -> tuple[str, str]:
        """Which form of the reference the voice is given, by the evidence for this exact written form, voice and model: a validated form first; a form
        that failed is not chosen again while another has not failed; otherwise the configured default (a choice by reasoning, said so: no audio has
        compared the forms yet)."""
        tried = {} if self.memory is None else {
            i.replacement: i for i in self.db.query(PronunciationIntervention).filter_by(exact_form=word.surface, voice_id=self.voice_id, model_id=self.model_id)}
        for form, text in candidates.items():
            if text in tried and tried[text].status == "validated":
                return form, f"validated intervention {tried[text].id}"
        healthy = [f for f, t in candidates.items() if not (t in tried and tried[t].failures)]
        default = self.cfg.tts_marking if self.cfg.tts_marking in candidates else next(iter(candidates))
        if default in healthy:
            return default, "the configured default; no audio has compared the forms yet"
        if healthy:
            return healthy[0], f"the {default} form failed with this voice"
        return default, "every form failed with this voice: the default, for a person to review"

    def limit(self, decisions: list[Decision]) -> None:
        """Keeps the unvalidated changes of a block within the naturalness budget (the riskiest first) and out of long runs."""
        budget = max(1, int(self.cfg.max_changed_fraction * len(decisions)))
        candidates = sorted((d for d in decisions if d.strategy in CHANGES and not d.validated), key=lambda d: (d.level != "CRITICAL", d.n))
        for extra in candidates[budget:]:
            extra.strategy, extra.replacement, extra.basis = "keep", None, f"over the naturalness budget of the block ({budget} unvalidated changes): {extra.basis}"
        run = 0
        for d in decisions:
            run = run + 1 if d.strategy in CHANGES else 0
            if run > self.cfg.max_changed_in_a_row and not d.validated:
                d.strategy, d.replacement, d.basis, run = "keep", None, f"a long run of changed words: {d.basis}", 0

    # --- memory ----------------------------------------------------------------------------------------------------------------------

    def register(self, plan: BlockPlan) -> None:
        """Keeps each candidate (an unvalidated change) as a candidate intervention of a candidate entry, so that what the voice does with it can be
        booked. Never touches an entry a person has decided about. A PPIE run keeps nothing."""
        if self.memory is None:
            return
        words = tokenize(plan.text)
        for d in plan.decisions:
            if d.strategy not in ("selective_marks", "ezafe_mark") or d.intervention_id is not None:
                continue
            context: dict = {}
            b = plan.boundaries[d.n] if plan.boundaries else None
            grammar = {"reference": d.reference.describe()} if d.reference is not None else {}
            if d.strategy == "ezafe_mark" and b is not None:      # an ezafe is kept with its context: the next word, the relation, the sentence
                context = {"next_word": key_of(b.next_word), "sense": ezafe_sense(b), "sentence": " ".join(w.plain for w in words)}
                grammar["ezafe"] = b.describe()
            if grammar:
                context["grammar"] = grammar
            proposal = self.memory.propose(language=self.language, surface=d.surface, expected_irab=d.replacement or "", actor="agent:planner", voice_id=self.voice_id,
                                           model_id=self.model_id, scope="exact_form" if not context else "context", **context)
            if proposal.blocked_by is None:
                d.intervention_id = self.memory.add_intervention(proposal.entry.id, kind="irab", exact_form=d.surface, replacement=d.replacement or "",
                                                                 voice_id=self.voice_id, model_id=self.model_id).id

    def learn(self, block_id: str, surface: str, *, heard_right: bool, case_id: int | None = None) -> None:
        """What the listening QA found for a word of the plan. A failure is booked against the intervention and as evidence (the next risk assessment
        counts it); a success is evidence only, unless the config lets a listening model validate. Nothing here approves anything. A PPIE run keeps
        nothing."""
        if self.memory is None:
            return
        plan = self.plans.get(block_id)
        decision = next((d for d in plan.decisions if key_of(d.surface) == key_of(surface)), None) if plan else None
        if decision is None:
            return
        entry_id = None
        if decision.intervention_id is not None:
            entry_id = self.db.get(PronunciationIntervention, decision.intervention_id).entry_id
            if not heard_right or self.cfg.validate_from_listener:
                self.memory.record_outcome(decision.intervention_id, success=heard_right)
        elif not heard_right:
            reading = next((r.reading for r in plan.risks if r.n == decision.n), "")
            try:
                expected = spelling.persian(reading)
            except ValueError:
                expected = decision.surface
            proposal = self.memory.propose(language=self.language, surface=surface, expected_irab=expected, actor="agent:planner", voice_id=self.voice_id,
                                           model_id=self.model_id, scope="exact_form", case_id=case_id)
            entry_id = proposal.entry.id
        if entry_id is not None:
            self.memory.add_evidence(surface=surface, kind="model", verdict="match" if heard_right else "mismatch", entry_id=entry_id, case_id=case_id,
                                     block_id=block_id, voice_id=self.voice_id, model_id=self.model_id, detail=f"strategy {decision.strategy}")

    def report(self) -> dict:
        """The plans of the render as a file: every non-keep decision with its basis, the escalations, and the naturalness of each block."""
        return {"voice_id": self.voice_id, "model_id": self.model_id, "blocks": {b: p.describe() for b, p in self.plans.items() if p.decisions},
                "escalations": self.escalations()}

    def escalations(self) -> list[dict]:
        """The words left for a person, each once with the blocks it is in."""
        found: dict[str, dict] = {}
        for b, p in self.plans.items():
            for d in p.decisions:
                if d.strategy == "escalate" or d.needs_review:
                    item = found.setdefault(key_of(d.surface), {"surface": d.surface, "level": d.level, "basis": d.basis, "blocks": []})
                    item["blocks"].append(b)
        return sorted(found.values(), key=lambda i: -len(i["blocks"]))
