"""How long a detective-dispatched graphic needs to be read, and whether the narration over it competes
(Section F, checklist row F2).

The graphics layer already knows a text-heavy card needs longer than a one-line card: its streaming
schedule (`graphics.shots.schedule_of`) reveals words at the configured reading pace and `planned_seconds`
adds a margin. Section D's `place_detective_graphics` used the per-kind constant instead, so a dense card
was carved too short and `finalize` later dropped it. This module gives the dispatcher the real numbers and
the "scheduling request" a too-short or competing placement must report instead of failing silently."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.documentary.graphics import shots as GS
from app.documentary.graphics import theme as T
from app.documentary.mvp.direct import content_words


@dataclass(frozen=True)
class ReadingNeed:
    words: int         # words the viewer is asked to read, from the real streamed layout
    planned: float     # seconds to hold the card so it streams at the reading pace (with the margin)
    fastest: float     # shortest shot the card still streams in; below this `finalize` drops it


def reading_need(kind: str, data: dict, language: str) -> ReadingNeed:
    """Raises GraphicUnavailable when the card cannot be streamed at all."""
    cfg = T.config().stream
    if not cfg.enabled:
        hold = GS.hold_seconds(kind)
        return ReadingNeed(0, hold, hold)
    sched = GS.schedule_of(kind, data, language)
    return ReadingNeed(sum(sched.counts), GS.planned_seconds(kind, data, language),
                       round(sched.total * cfg.min_speed_scale, 3))


def timing_request(action_key: str, beat_id: str, kind: str, problem: str, needed: float,
                   available: float, requested_change: str) -> dict:
    """The Visual Design Director's `timing_requests` shape (affected action, the reading problem, the
    scheduling change asked for), so the Main Visual Planner can act on it rather than have it vanish."""
    return {"action_key": action_key, "beat_id": beat_id, "kind": kind, "problem": problem,
            "needed_seconds": round(needed, 2), "available_seconds": round(available, 2),
            "requested_change": requested_change}


def visible_text(data) -> str:
    """Every string a graphic's data carries, as one text (used only to ask whether narration is about it)."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return " ".join(visible_text(v) for v in data.values())
    if isinstance(data, (list, tuple)):
        return " ".join(visible_text(v) for v in data)
    return ""


def speech_over(shots: list[dict], start: float, end: float) -> tuple[int, str]:
    """(word count, text) of the narration spoken in [start, end]: each shot's `said`, in proportion to how
    much of that shot the window covers (a shot carries its sentence, not per-word times)."""
    words, texts = 0.0, []
    for sh in shots:
        said = (sh.get("said") or "").strip()
        length = sh["end"] - sh["start"]
        overlap = min(end, sh["end"]) - max(start, sh["start"])
        if not said or length <= 0 or overlap <= 0:
            continue
        share = overlap / length
        words += len(said.split()) * share
        texts.append(said)
    return round(words), " ".join(texts)


def competing_speech(card_words: int, card_data: dict, shots: list[dict], start: float, end: float) -> dict | None:
    """A finding when a dense card is read while dense narration it is not about is spoken over it
    (the doc: "avoid making them read a dense board while listening to different dense information")."""
    cfg = ai_config.detective_toolkit.reading
    seconds = end - start
    if card_words < cfg.dense_card_words or seconds <= 0:
        return None
    spoken_words, spoken = speech_over(shots, start, end)
    rate = spoken_words / seconds
    if rate < cfg.dense_speech_words_per_second:
        return None
    card = content_words(visible_text(card_data))
    overlap = len(card & content_words(spoken)) / len(card) if card else 0.0
    if overlap >= cfg.related_overlap_min:
        return None
    return {"speech_words_per_second": round(rate, 2), "card_words": card_words,
            "shared_content_fraction": round(overlap, 2)}

