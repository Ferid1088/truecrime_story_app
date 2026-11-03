"""The pronunciation risk of a word, found from what the engine knows about it and from its structure, never from a list of known faults: a word the
evidence and the writer disagree on, a word with no evidence at all (names, new words), a word with several readings, a host word with a clitic,
a hard cluster of consonants. The weights are estimates until calibrated against labelled examples; the score says "worth a rule", not "wrong"."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.reading import compiler
from app.documentary.reading.engine import Outcome


@dataclass
class Risk:
    score: float
    factors: dict[str, float] = field(default_factory=dict)

    @property
    def category(self) -> str:
        return max(self.factors, key=self.factors.get) if self.factors else "none"


def consonant_run(reading: str) -> int:
    longest = run = 0
    for symbol in compiler.symbols(reading.replace("-", "")):
        run = run + 1 if symbol in compiler.CONSONANTS and symbol != "'" else 0
        longest = max(longest, run)
    return longest


def analyze(outcome: Outcome) -> Risk:
    cfg = ai_config.reading.risk
    w, found = cfg.weights, {}
    if outcome.reading == ai_config.reading.transcription.number:
        return Risk(0.0)
    if outcome.basis == "exception":
        found["verified_exception"] = w["verified_exception"]
    if outcome.status == "resolved" and outcome.basis != "exception":
        found["resolved_against_writer"] = w["resolved_against_writer"]
    if outcome.status == "uncertain" and outcome.conflict:
        found["uncertain_conflict"] = w["uncertain_conflict"]
    if outcome.status == "unverified":
        found["no_evidence"] = w["no_evidence"]
    if outcome.homograph and len(outcome.candidates) > 1:
        found["ambiguous_entries"] = w["ambiguous_entries"]
    if outcome.basis == "rule":
        found["clitic_rule"] = w["clitic_rule"]
    bare = outcome.reading.split("|")[0]
    if consonant_run(bare) >= cfg.cluster_min_consonants:
        found["consonant_cluster"] = w["consonant_cluster"]
    if any(s in bare for s in cfg.rare_sounds):
        found["glottal_or_rare_sound"] = w["glottal_or_rare_sound"]
    if len(outcome.surface) >= cfg.long_word_letters:
        found["long_word"] = w["long_word"]
    return Risk(round(1 - math.prod(1 - v for v in found.values()), 4), found)
