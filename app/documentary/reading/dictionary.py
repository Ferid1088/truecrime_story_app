"""The pronunciation rules of ONE block (never shared with another): the words of the block whose risk is above the threshold and whose reading the
engine can stand behind, each as an ElevenLabs alias rule in the configured strategy. A rule that would be unsafe (the word twice with two
replacements, a replacement equal to the word, a word not in the text, more rules than the block may have) is not made and the reason is kept."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai_config import ai_config
from app.documentary.reading import compiler, scheme
from app.documentary.reading.engine import Outcome
from app.documentary.reading.risk import Risk, analyze
from app.documentary.reading.transcription import join


@dataclass
class Rules:
    strategy: str
    rules: list[dict] = field(default_factory=list)
    chosen: list[dict] = field(default_factory=list)       # for each rule: the word, its status, its risk and why
    skipped: list[dict] = field(default_factory=list)


def finglish(reading: str) -> str:
    """The reading as Latin text for an alias: no pause, the ezafe written as part of the word, a hyphen only where it keeps kh/gh/zh/sh/ch apart."""
    stem, pause = scheme.split_pause(reading)
    stem, ezafe = scheme.split_ezafe(stem)
    return join(f"{stem}-{ezafe}" if ezafe else stem)


def persian(reading: str) -> str:
    return compiler.to_persian(reading)


STRATEGIES = {"finglish_alias": finglish, "persian_diacritic_alias": persian}


def build(outcomes: list[Outcome], text: str, strategy: str | None = None) -> Rules:
    cfg = ai_config.reading.risk
    strategy = strategy or cfg.active_strategy
    out = Rules(strategy)
    if strategy == "none":
        return out
    make = STRATEGIES[strategy]
    scored: list[tuple[Outcome, Risk]] = [(o, analyze(o)) for o in outcomes]
    by_surface: dict[str, set[str]] = {}
    for o, risk in scored:
        if risk.score >= cfg.threshold and o.status in cfg.alias_statuses:
            by_surface.setdefault(o.surface, set()).add(make(o.reading))
    taken: set[str] = set()
    for o, risk in sorted(scored, key=lambda x: -x[1].score):
        if risk.score < cfg.threshold:
            continue
        note = {"block": o.block_id, "n": o.n, "word": o.surface, "reading": o.reading, "status": o.status, "risk": risk.score, "factors": risk.factors}
        if o.status not in cfg.alias_statuses:
            out.skipped.append({**note, "why": f"the engine does not stand behind this reading (status {o.status})"})
            continue
        if o.surface in taken:
            continue
        if len(by_surface[o.surface]) > 1:
            out.skipped.append({**note, "why": "the same word has different readings in this block: a rule cannot tell them apart"})
            continue
        if o.surface not in text:
            out.skipped.append({**note, "why": "the written word is not in the voiced text"})
            continue
        if any(o.surface in other or other in o.surface for other in taken):
            out.skipped.append({**note, "why": "its letters overlap a rule already made for this block"})
            continue
        alias = next(iter(by_surface[o.surface]))
        if alias == o.surface:
            out.skipped.append({**note, "why": "the replacement is the word itself"})
            continue
        if len(out.rules) >= cfg.max_rules_per_block:
            out.skipped.append({**note, "why": "the block already has its most rules"})
            continue
        taken.add(o.surface)
        out.rules.append({"type": "alias", "string_to_replace": o.surface, "alias": alias})
        out.chosen.append({**note, "alias": alias})
    return out
