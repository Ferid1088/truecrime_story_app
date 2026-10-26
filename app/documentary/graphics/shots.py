"""Graphics inside the film: a planned graphic shot carries its type, its (already localized) data and the cue
positions as fractions of its length; once the film's timeline is final, the cues become seconds and the
graphic is drawn once to prove it fits. The render engine plays a graphic shot as a still page that only
changes on its cues."""

from __future__ import annotations

import numpy as np

from app.documentary.graphics import theme as T
from app.documentary.graphics.base import GraphicUnavailable, Rendered
from app.documentary.graphics.registry import graphic


def cue_fractions(kind: str, steps: int) -> list[float]:
    """Where the reveals fall in the shot (fractions of its length): the configured spacing, spread evenly when a
    card has more steps than the configured list (a timeline of five events)."""
    configured = T.config().cue_fractions[kind]
    if len(configured) >= steps:
        return list(configured[:steps])
    last = configured[-1] if configured else 0.7
    first = configured[0] if configured else 0.08
    return [round(first + (last - first) * n / max(steps - 1, 1), 3) for n in range(steps)]


def plan_fields(kind: str, data: dict) -> dict:
    """The shot fields the planner stores for a graphic."""
    return {"graphic": {"type": kind, "data": data, "cue_fractions": cue_fractions(kind, graphic(kind).required_cues(data))}}


def hold_seconds(kind: str) -> float:
    return T.config().hold_seconds[kind]


def cues_for(spec: dict, seconds: float) -> list[float]:
    need = graphic(spec["type"]).required_cues(spec["data"])
    return [round(f * seconds, 3) for f in spec["cue_fractions"][:need]]


def render_for(spec: dict, seconds: float, language: str) -> Rendered:
    return graphic(spec["type"]).render(spec["data"], seconds, cues_for(spec, seconds), language)


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
        spec["cues"] = cues_for(spec, shot["end"] - shot["start"])
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
