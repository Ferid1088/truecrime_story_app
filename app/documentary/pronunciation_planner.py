"""The adaptive TTS intervention planner: for every word of a block the smallest change to what the voice is given that its risk and the memory justify,
and nothing for the words that need none. The block's text is the original text; only the words with a decision other than `keep` differ.

Strategies, in the order they are tried for a word:
  respelling       a pronunciation a person (or the app) approved, or a validated respelling, for this exact written form in this context;
  dictionary_rule  a validated dictionary alias for it (only when the language's voice has a dictionary);
  selective_marks  the word's own letters with their short vowels as marks, only for a HIGH or CRITICAL word whose reading can be written on its own
                   letters (a candidate: nothing says yet that the voice obeys it);
  escalate         no safe change: the word stays as written and is reported for a person;
  keep             the rest. LOW is no proof, MEDIUM is left to the listening QA.
A candidate never becomes an approval here. How many unvalidated changes a block may carry is limited (config), so that the speech stays natural."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.documentary.pronunciation_memory import APPROVED, PronunciationMemory
from app.documentary.pronunciation_risk import RiskEngine, WordRisk
from app.documentary.reading import spelling
from app.documentary.reading.voicing import Voicing
from app.documentary.reading.words import VOWEL_MARKS, Word, key_of, substitute, tokenize

CHANGES = ("respelling", "dictionary_rule", "selective_marks")
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

    def describe(self) -> dict:
        return {"n": self.n, "surface": self.surface, "level": self.level, "strategy": self.strategy, "replacement": self.replacement, "basis": self.basis,
                "validated": self.validated, "needs_review": self.needs_review, "intervention_id": self.intervention_id}


@dataclass
class BlockPlan:
    block_id: str
    text: str
    rules: list[dict] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    risks: list[WordRisk] = field(default_factory=list)
    note: str = ""

    @property
    def naturalness(self) -> dict:
        return naturalness(self.decisions)

    def describe(self) -> dict:
        return {"block_id": self.block_id, "note": self.note, "naturalness": self.naturalness, "rules": self.rules,
                "words": [{"risk": r.describe(), "decision": d.describe()} for r, d in zip(self.risks, self.decisions, strict=True)]}


def naturalness(decisions: list[Decision]) -> dict:
    """How much of the block was changed, measured on the decisions alone (not on the audio: how natural the voice sounds is for the listening QA)."""
    changed = [d.strategy in CHANGES for d in decisions]
    run = longest = 0
    for c in changed:
        run = run + 1 if c else 0
        longest = max(longest, run)
    return {"words": len(decisions), "changed": sum(changed), "changed_fraction": round(sum(changed) / len(decisions), 3) if decisions else 0.0,
            "longest_run": longest, "unvalidated_changed": sum(1 for d, c in zip(decisions, changed, strict=True) if c and not d.validated)}


def skeleton(text: str) -> str:
    return JOINERS.sub("", key_of(VOWEL_MARKS.sub("", text)))


class Planner:
    def __init__(self, db: Session, language: str, voice_id: str, model_id: str):
        self.db, self.language, self.voice_id, self.model_id = db, language, voice_id, model_id
        self.memory, self.cfg = PronunciationMemory(db), ai_config.pronunciation_risk
        self.engine: RiskEngine | None = None
        self.plans: dict[str, BlockPlan] = {}

    # --- planning --------------------------------------------------------------------------------------------------------------------

    def plan(self, block_id: str, text: str, voicing: Voicing, allow_dictionary: bool = False, covered: frozenset[str] = frozenset()) -> BlockPlan:
        """`covered`: the plain forms of the words the language's dictionary already has a rule for."""
        words, readings = tokenize(text), voicing.readings.get(block_id)
        if readings is None or len(readings) != len(words):
            return self.keep_all(block_id, text, "no reading of this block, or not the words of its reading: left as written")
        if self.engine is None:
            self.engine = RiskEngine(self.db, self.language, self.voice_id, self.model_id, voicing.names)
        risks = self.engine.assess(words, readings, voicing.signals.get(block_id))
        decisions = [self.decide(w, r, readings[w.n], words, voicing, allow_dictionary, covered) for w, r in zip(words, risks, strict=True)]
        self.limit(decisions)
        plan = BlockPlan(block_id, substitute(text, {d.n: d.replacement for d in decisions if d.strategy in ("respelling", "selective_marks") and d.replacement}),
                         [self.rule(d) for d in decisions if d.strategy == "dictionary_rule"], decisions, risks)
        self.plans[block_id] = plan
        return plan

    def keep_all(self, block_id: str, text: str, note: str) -> BlockPlan:
        plan = BlockPlan(block_id, text, note=note)
        self.plans[block_id] = plan
        return plan

    @staticmethod
    def rule(d: Decision) -> dict:
        return {"string_to_replace": d.surface, "type": "alias", "alias": d.replacement}

    def decide(self, word: Word, risk: WordRisk, reading: str, words: list[Word], voicing: Voicing, allow_dictionary: bool, covered: frozenset[str]) -> Decision:
        def made(strategy: str, basis: str, **fields) -> Decision:
            return Decision(word.n, word.surface, risk.level, strategy, basis, **fields)

        if word.number or reading == "NUM":
            return made("keep", "a number, read by code")
        if word.key in voicing.fixed:
            spelled = Voicing.spell(Voicing.reading_of(voicing.fixed[word.key], reading))
            return made("respelling", "the reading a person approved (or the reading loop verified) for this word", replacement=spelled, validated=True)
        prev_key = key_of(words[word.n - 1].surface) if word.n else ""
        next_key = key_of(words[word.n + 1].surface) if word.n + 1 < len(words) else ""
        context = {"prev_word": prev_key, "next_word": next_key, "voice_id": self.voice_id, "model_id": self.model_id}
        for i in self.memory.reusable(self.language, word.surface, **context):
            strategy = KIND_STRATEGY[i.kind]
            if strategy == "dictionary_rule" and not allow_dictionary:
                strategy = "respelling"                           # the alias is the spelling itself
            return made(strategy, f"validated intervention {i.id} ({i.kind}) for this exact form, voice and model", replacement=i.replacement, validated=True,
                        intervention_id=i.id)
        for m in self.memory.find(self.language, word.surface, **context):
            if m.entry.status in APPROVED and m.entry.approved_pronunciation:
                return made("respelling", f"entry {m.entry.id} approved ({m.entry.status}), matching context ({m.reason})", replacement=m.entry.approved_pronunciation, validated=True)
        if risk.level in ("HIGH", "CRITICAL") and word.key in covered:
            return made("keep", f"{risk.level} risk, but the language's dictionary already has a rule for this word: the planner adds nothing on top of it")
        if risk.level in ("LOW", "MEDIUM"):
            return made("keep", "low risk: no reason to change the text" if risk.level == "LOW" else "medium risk: left as written, the listening QA checks it")
        marked = self.marks(word, reading)
        if marked is None:
            return made("escalate", f"{risk.level} risk, and the reading cannot be written on the word's own letters: {risk.rationale}", needs_review=True)
        return made("selective_marks", f"{risk.level} risk: {risk.rationale}", replacement=marked, needs_review=risk.level == "CRITICAL")

    @staticmethod
    def marks(word: Word, reading: str) -> str | None:
        """The word's own letters with the short vowels of its reading as marks, or None when the reading cannot be written on them (another letter, a
        colloquial spelling, a word that already has marks): then nothing is invented."""
        if word.surface != word.plain:
            return None
        try:
            marked = spelling.persian(reading)
        except ValueError:
            return None
        return marked if skeleton(marked) == skeleton(word.surface) and marked != word.surface else None

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
        booked. Never touches an entry a person has decided about."""
        for d in plan.decisions:
            if d.strategy != "selective_marks" or d.intervention_id is not None:
                continue
            proposal = self.memory.propose(language=self.language, surface=d.surface, expected_irab=d.replacement or "", actor="agent:planner", voice_id=self.voice_id,
                                           model_id=self.model_id, scope="exact_form")
            if proposal.blocked_by is None:
                d.intervention_id = self.memory.add_intervention(proposal.entry.id, kind="irab", exact_form=d.surface, replacement=d.replacement or "",
                                                                 voice_id=self.voice_id, model_id=self.model_id).id

    def learn(self, block_id: str, surface: str, *, heard_right: bool, case_id: int | None = None) -> None:
        """What the listening QA found for a word of the plan. A failure is booked against the intervention and as evidence (the next risk assessment
        counts it); a success is evidence only, unless the config lets a listening model validate. Nothing here approves anything."""
        plan = self.plans.get(block_id)
        decision = next((d for d in plan.decisions if key_of(d.surface) == key_of(surface)), None) if plan else None
        if decision is None:
            return
        entry_id = None
        if decision.intervention_id is not None:
            from app.db.models import PronunciationIntervention

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
