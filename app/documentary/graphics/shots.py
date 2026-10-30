"""Graphics inside the film: a planned graphic shot carries its type, its (already localized) data and the cue
positions as fractions of its length; once the film's timeline is final, the cues become seconds and the
graphic is drawn once to prove it fits. The render engine plays a graphic shot as a still page that only
changes on its cues."""

from __future__ import annotations

import json

import numpy as np

from app.documentary.graphics import theme as T
from app.documentary.graphics.base import GraphicUnavailable, Rendered
from app.documentary.graphics.page import DoesNotFit
from app.documentary.graphics.registry import graphic
from app.documentary.graphics.streaming import Schedule, StreamError, schedule

ENGINE_KINDS = frozenset({"forensic", "evidence_check"})


def cue_fractions(kind: str, steps: int) -> list[float]:
    """Where the reveals fall in the shot (fractions of its length): the configured spacing, spread evenly when a
    card has more steps than the configured list (a timeline of five events)."""
    configured = T.config().cue_fractions[kind]
    span = T.config().engines["cue_span"]
    if kind in ENGINE_KINDS:                 # the engines spread their steps over the whole shot
        first, last = span["first"], span["last"]
        return [round(first + (last - first) * n / max(steps - 1, 1), 3) for n in range(steps)]
    if len(configured) >= steps:
        return list(configured[:steps])
    last = configured[-1] if configured else 0.7
    first = configured[0] if configured else 0.08
    return [round(first + (last - first) * n / max(steps - 1, 1), 3) for n in range(steps)]


def plan_fields(kind: str, data: dict) -> dict:
    """The shot fields the planner stores for a graphic."""
    return {"graphic": {"type": kind, "data": data, "cue_fractions": cue_fractions(kind, graphic(kind).required_cues(data))}}


def hold_seconds(kind: str) -> float:
    """The shortest a card is held (every kind alike)."""
    return T.config().card_timing["min_seconds"]


def schedule_of(kind: str, data: dict, language: str = "en") -> Schedule:
    """When the steps of the graphic start and how long it needs at the streaming pace, in this language."""
    try:
        return schedule(kind, json.dumps(data, sort_keys=True, ensure_ascii=False), language)
    except (DoesNotFit, StreamError) as e:
        raise GraphicUnavailable(f"{kind}: {e}") from e


def reading_seconds(kind: str, data: dict, language: str = "en") -> float:
    """How long a normal reader needs to see and read all the text of the card, and the card to stream it at the streaming pace:
    a base time and the words at a reading speed, never less than the time the card needs to stream all its text."""
    cfg = T.config().card_timing
    sched = schedule_of(kind, data, language)
    streamed = sched.total - T.config().stream.end_hold_seconds + 2.0          # the last word is in, and the viewer has 2 s more
    return max(cfg["read_base_seconds"] + sum(sched.counts) / cfg["read_words_per_second"], streamed)


def planned_seconds(kind: str, data: dict, language: str = "en") -> float:
    """How long the card is held: 15 to 20 seconds for the toolkit; when the text needs longer the planner extends it by up
    to 5 more. A card whose text needs more than that cannot be shown (the toolkit splits it)."""
    cfg = T.config().card_timing
    needed = reading_seconds(kind, data, language)
    if needed > cfg["max_seconds"] + cfg["extension_seconds"]:
        raise GraphicUnavailable(f"{kind}: its text needs {needed:.0f}s to read, more than the "
                                 f"{cfg['max_seconds'] + cfg['extension_seconds']:.0f}s a card may be held")
    return round(min(max(needed, cfg["min_seconds"]), cfg["max_seconds"] + cfg["extension_seconds"]), 1)


def cues_for(spec: dict, seconds: float, language: str = "en") -> list[float]:
    """The cue times of the shot. A shot shorter than the schedule streams faster, down to `stream.min_speed_scale`."""
    need = graphic(spec["type"]).required_cues(spec["data"])
    cfg = T.config().stream
    if not cfg.enabled:
        return [round(f * seconds, 3) for f in spec["cue_fractions"][:need]]
    sched = schedule_of(spec["type"], spec["data"], language)
    cues = list(sched.cues)[:need]
    if seconds >= sched.total:
        return cues
    scale = seconds / sched.total
    if scale < cfg.min_speed_scale:
        raise GraphicUnavailable(f"{spec['type']}: {seconds:.1f}s is too short to stream its text (it needs "
                                 f"{sched.total * cfg.min_speed_scale:.1f}s at the fastest)")
    return [round(c * scale, 3) for c in cues]


def render_for(spec: dict, seconds: float, language: str) -> Rendered:
    return graphic(spec["type"]).render(spec["data"], seconds, cues_for(spec, seconds, language), language)


def finalize(shots: list[dict], language: str) -> list[dict]:
    """Give every graphic shot its cue times in seconds. A graphic that no longer fits its (final) length is
    removed from `shots` (the previous shot then holds) and returned so the film's checks report it."""
    dropped = []
    for shot in list(shots):
        if shot.get("kind") != "graphic":
            continue
        spec = shot["graphic"]
        try:
            render_for(spec, shot["end"] - shot["start"], language)
        except GraphicUnavailable as e:
            shots.remove(shot)
            dropped.append({"type": spec["type"], "start": shot["start"], "end": shot["end"], "why": str(e)})
            if shots and shots[-1]["end"] <= shot["start"] + 1e-6:
                shots[-1]["end"] = shot["end"]
            continue
        spec["cues"] = cues_for(spec, shot["end"] - shot["start"], language)
        spec["language"] = language
    return dropped


class GraphicCard:
    """The engine's object for a graphic shot (like the chapter and timeline cards)."""

    def __init__(self, shot: dict, language: str, width: int, height: int):
        spec = shot["graphic"]
        seconds = float(shot["end"]) - float(shot["start"])
        self.rendered = graphic(spec["type"]).render(spec["data"], seconds, spec["cues"], language)
        self.size = (width, height)

    def frame(self, since: float, progress: float) -> np.ndarray:
        frame = self.rendered.frame_at(since)
        if frame.shape[1] == self.size[0] and frame.shape[0] == self.size[1]:
            return frame
        import cv2

        return cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
