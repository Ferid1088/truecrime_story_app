"""The lengths each stage writes to, from config only."""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.core.ai_config import ai_config


@dataclass(frozen=True)
class LengthRange:
    language: str
    minutes_min: float
    minutes_max: float
    words_per_minute: int
    # Set for the gate that follows the one length correction (see after_correction).
    lower_tolerance: float | None = None

    @property
    def words_min(self) -> int:
        return round(self.minutes_min * self.words_per_minute)

    @property
    def words_max(self) -> int:
        return round(self.minutes_max * self.words_per_minute)

    @property
    def gate_words_min(self) -> int:
        tolerance = ai_config.language_pipeline.final_length_tolerance if self.lower_tolerance is None \
            else self.lower_tolerance
        return int(self.words_min * (1 - tolerance))

    def after_correction(self) -> "LengthRange":
        """The same range for the gate that judges the corrected text: its lower bound accepts the
        configured shortfall (length_correction.accepted_shortfall); the upper bound is unchanged."""
        return replace(self, lower_tolerance=ai_config.language_pipeline.length_correction.accepted_shortfall)

    @property
    def gate_words_max(self) -> int:
        return int(self.words_max * (1 + ai_config.language_pipeline.final_length_tolerance))

    def as_dict(self) -> dict:
        return {"target_minutes_min": self.minutes_min, "target_minutes_max": self.minutes_max,
                "target_words_min": self.words_min, "target_words_max": self.words_max}


def words_per_minute(language: str) -> int:
    return ai_config.language_pipeline.words_per_minute_for(language)


def minutes_of(text: str, language: str) -> float:
    return round(len((text or "").split()) / words_per_minute(language), 1)


def final_range(language: str, requested_minutes: float | None = None) -> LengthRange:
    """Stages 2-4: the requested minutes up to the headroom above them."""
    low, high = ai_config.language_pipeline.minutes_range(requested_minutes)
    return LengthRange(language, low, high, words_per_minute(language))


def act_word_targets(plan_words: dict[str, float], span: LengthRange, cap_ratio: float) -> dict[str, int]:
    """The words each act is written to: the plan's own act budgets scaled so that they add up to
    the middle of the language's allowed word range, no act above `cap_ratio` times the average act
    (what an act would lose to the cap goes to the others, in proportion). The same function serves
    every language."""
    if not plan_words:
        return {}
    total = round((span.words_min + span.words_max) / 2)
    cap = cap_ratio * total / len(plan_words)
    weights = {k: max(float(v), 0.0) for k, v in plan_words.items()}
    if not sum(weights.values()):
        weights = {k: 1.0 for k in weights}
    exact: dict[str, float] = {}
    free, remaining = dict(weights), float(total)
    while free:
        scale = remaining / sum(free.values())
        capped = {k for k, w in free.items() if w * scale > cap}
        if not capped:
            exact.update({k: w * scale for k, w in free.items()})
            break
        for k in capped:
            exact[k] = cap
            remaining -= cap
            del free[k]
    targets = {k: int(v) for k, v in exact.items()}
    spare = total - sum(targets.values())
    by_fraction = sorted(exact, key=lambda k: exact[k] - targets[k], reverse=True)
    for k in by_fraction:
        if spare <= 0:
            break
        if targets[k] + 1 <= cap:
            targets[k] += 1
            spare -= 1
    return {k: targets[k] for k in plan_words}


def act_length_report(targets: dict[str, int], sections: list[dict]) -> dict:
    """Words against target per act. Information only; the blocking length gate is the total."""
    tolerance = ai_config.language_pipeline.act_length_tolerance
    words = {s["id"]: len(s["text"].split()) for s in sections}
    return {act: {"target": target, "words": words.get(act, 0),
                  "ratio": round(words.get(act, 0) / target, 2) if target else None,
                  "within_tolerance": bool(target) and abs(words.get(act, 0) - target) <= tolerance * target}
            for act, target in targets.items()}


def draft_words() -> int:
    cfg = ai_config.language_pipeline
    return cfg.long_draft_minutes * words_per_minute(cfg.draft_language)


def draft_range(requested_minutes: float | None = None) -> LengthRange:
    """The draft's own span: from the longest final text it has to feed up to the configured long
    draft. The draft is written long on purpose; this is the range its review is held to."""
    cfg = ai_config.language_pipeline
    _, high = cfg.minutes_range(requested_minutes)
    return LengthRange(cfg.draft_language, high, cfg.long_draft_minutes, words_per_minute(cfg.draft_language))


def draft_min_words(requested_minutes: float | None = None) -> int:
    """The shortest acceptable draft: as long as the longest final text it has to feed."""
    return final_range(ai_config.language_pipeline.draft_language, requested_minutes).words_max


def check_requested_minutes(requested: float) -> None:
    """The draft has to be at least as long as the longest final text it feeds."""
    cfg = ai_config.language_pipeline
    _, high = cfg.minutes_range(requested)
    if requested < ai_config.story.min_target_minutes or high > cfg.long_draft_minutes:
        raise ValueError(
            f"requested minutes must lie between {ai_config.story.min_target_minutes} and "
            f"{cfg.long_draft_minutes - cfg.rewrite_headroom_minutes} (the {cfg.long_draft_minutes}-minute "
            f"draft has to cover the requested length plus {cfg.rewrite_headroom_minutes} minutes)")
