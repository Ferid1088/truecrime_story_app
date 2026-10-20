"""Host-led reel structure: Fereidoun opens every reel (S1/owner decision 1), the segment
timing must fit inside the hard duration cap and the avatar-seconds cap, the hook line must pass
the existing safety gates plus the banned-phrase/greeting checks, and consecutive reels must
never reuse the same (pose, outfit, camera) combination (automation-feel, S1/S9)."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ai_config import ai_config


@dataclass(frozen=True)
class ReelTiming:
    cover_seconds: float
    hook_seconds: float
    film_cut_seconds: float
    outro_seconds: float
    end_card_seconds: float

    @property
    def avatar_seconds(self) -> float:
        return self.hook_seconds + self.outro_seconds

    @property
    def total_seconds(self) -> float:
        return (self.cover_seconds + self.hook_seconds + self.film_cut_seconds
                + self.outro_seconds + self.end_card_seconds)


class ReelTimingError(ValueError):
    pass


def build_timing(*, hook_seconds: float, film_cut_seconds: float, outro_seconds: float,
                 cover_seconds: float = 0.0) -> ReelTiming:
    cfg = ai_config.shorts_host_led
    if not (cfg.hook_seconds_min <= hook_seconds <= cfg.hook_seconds_max):
        raise ReelTimingError(
            f"hook is {hook_seconds}s, must be {cfg.hook_seconds_min}-{cfg.hook_seconds_max}s")
    if not (cfg.outro_seconds_min <= outro_seconds <= cfg.outro_seconds_max):
        raise ReelTimingError(
            f"outro is {outro_seconds}s, must be {cfg.outro_seconds_min}-{cfg.outro_seconds_max}s")
    timing = ReelTiming(cover_seconds=cover_seconds, hook_seconds=hook_seconds,
                        film_cut_seconds=film_cut_seconds, outro_seconds=outro_seconds,
                        end_card_seconds=cfg.end_card_seconds)
    if timing.avatar_seconds > cfg.avatar_max_seconds + 1e-9:
        raise ReelTimingError(
            f"avatar time {timing.avatar_seconds}s exceeds cap {cfg.avatar_max_seconds}s")
    if timing.total_seconds > cfg.hard_duration_max_seconds + 1e-9:
        raise ReelTimingError(
            f"reel is {timing.total_seconds}s, exceeds hard max {cfg.hard_duration_max_seconds}s")
    return timing


class HookRejected(ValueError):
    pass


def validate_hook_text(text: str, language: str) -> str:
    """The hook is the first thing heard after frame 0: no greeting/self-introduction, at most
    hook_max_words words, none of the banned hook phrases (case-insensitive substring)."""
    cfg = ai_config.shorts_host_led
    stripped = text.strip()
    if not stripped:
        raise HookRejected("empty hook")
    words = stripped.split()
    if len(words) > cfg.hook_max_words:
        raise HookRejected(f"hook has {len(words)} words, max {cfg.hook_max_words}")
    lowered = stripped.lower()
    for phrase in cfg.banned_hook_phrases:
        if phrase.lower() in lowered:
            raise HookRejected(f"hook contains banned phrase {phrase!r}")
    return stripped


Variant = tuple[str, str, str]  # (pose, outfit, camera_view)


def next_variant(available: list[Variant], previous: Variant | None, index: int = 0) -> Variant:
    """The variant at ``index`` in a deterministic round-robin over ``available`` (registry
    order), skipped forward by one only if it would repeat ``previous`` — which only happens
    when fewer than 2 distinct variants are registered."""
    if not available:
        raise ValueError("no avatar pose/outfit/camera variants registered")
    combo = available[index % len(available)]
    if combo == previous:
        if len(set(available)) < 2:
            raise ValueError("only one pose/outfit/camera variant is registered; need at "
                             "least 2 to avoid repeating the previous reel's look")
        combo = available[(index + 1) % len(available)]
    return combo


def variant_sequence(available: list[Variant], count: int) -> list[Variant]:
    """``count`` reels' variants, cycling through the registry, never the same combination
    twice in a row."""
    out: list[Variant] = []
    previous: Variant | None = None
    for i in range(count):
        previous = next_variant(available, previous, i)
        out.append(previous)
    return out
