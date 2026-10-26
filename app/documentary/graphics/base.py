"""The interface every code-made graphic has: it says whether it can be drawn (`can_render`), how many reveal
steps it has (`required_cues`) and draws frames for the duration and cue times the planner gives it (`render`).
Same data, cues and language always give the same frames. A graphic has no timing rules of its own."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod

import numpy as np
from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.page import DoesNotFit


class GraphicUnavailable(ValueError):
    """Why a graphic cannot be drawn from the data / duration / cues it was given (the planner uses the next fallback)."""


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def ease(x: float) -> float:
    x = clamp01(x)
    return x * x * (3 - 2 * x)


class Graphic(ABC):
    kind: str
    #: seconds the last cue keeps on screen before the graphic ends (after the cross-fade)
    minimum_rest_seconds = 1.5

    @abstractmethod
    def can_render(self, data: dict) -> str | None:
        """None when the data is complete and within the limits, else the plain reason it is not."""

    @abstractmethod
    def required_cues(self, data: dict) -> int:
        """Number of reveal steps (one cue each); state 0 is the page before the first cue."""

    def minimum_cues(self, data: dict) -> int:
        """Fewest cues the graphic accepts; the steps after them are skipped and reported."""
        return self.required_cues(data)

    @abstractmethod
    def draw_state(self, data: dict, language: str, step: int) -> Image.Image:
        """The page after `step` reveals (0 = before the first)."""

    def draw_between(self, data: dict, language: str, step: int, progress: float, before: Image.Image,
                     after: Image.Image) -> Image.Image:
        """The page while step `step` is being revealed (progress 0..1); cross-fade unless a graphic draws it."""
        return Image.blend(before, after, ease(progress))

    def render(self, data: dict, duration_s: float, cues: list[float], language: str) -> Rendered:
        reason = self.can_render(data)
        if reason:
            raise GraphicUnavailable(f"{self.kind}: {reason}")
        if language not in T.config().labels:
            raise GraphicUnavailable(f"{self.kind}: no labels for language {language}")
        need, least = self.required_cues(data), self.minimum_cues(data)
        if len(cues) < least:
            raise GraphicUnavailable(f"{self.kind}: needs {least} cue(s), the planner gave {len(cues)}")
        used = list(cues[:need])
        if any(b <= a for a, b in zip(used, used[1:], strict=False)) or (used and used[0] < 0):
            raise GraphicUnavailable(f"{self.kind}: cues must rise from 0")
        transition = T.config().transition_seconds
        if used and used[-1] + transition + self.minimum_rest_seconds > duration_s:
            raise GraphicUnavailable(
                f"{self.kind}: {duration_s:.1f}s is too short for its last cue at {used[-1]:.1f}s")
        skipped = [f"step {n + 1}" for n in range(len(used), need)]
        rendered = Rendered(self, data, language, float(duration_s), used, skipped)
        try:
            for step in range(len(used) + 1):          # every page is drawn now: a text that cannot fit fails here
                rendered.state(step)
        except DoesNotFit as e:
            raise GraphicUnavailable(f"{self.kind}: {e}") from e
        return rendered


class Rendered:
    """A graphic fitted to a duration and cues: asks for the frame at any time since its start."""

    def __init__(self, graphic: Graphic, data: dict, language: str, duration_s: float, cues: list[float],
                 skipped: list[str]):
        self.graphic, self.data, self.language = graphic, data, language
        self.duration_s, self.cues, self.skipped = duration_s, cues, skipped
        self.steps = len(cues)
        self._states: dict[int, Image.Image] = {}

    def key(self) -> str:
        return hashlib.sha256(json.dumps([self.graphic.kind, self.data, self.language, self.cues],
                                         sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def state(self, step: int) -> Image.Image:
        if step not in self._states:
            self._states[step] = self.graphic.draw_state(self.data, self.language, step)
        return self._states[step]

    def position(self, since: float) -> tuple[int, float]:
        """(step, progress of its reveal): step k is on screen once cue k-1 has passed."""
        step = sum(1 for c in self.cues if since >= c)
        if step == 0:
            return 0, 1.0
        return step, clamp01((since - self.cues[step - 1]) / T.config().transition_seconds)

    def image_at(self, since: float) -> Image.Image:
        step, progress = self.position(since)
        if step == 0 or progress >= 1.0:
            return self.state(step)
        return self.graphic.draw_between(self.data, self.language, step, progress, self.state(step - 1),
                                         self.state(step))

    def frame_at(self, since: float) -> np.ndarray:
        return np.asarray(self.image_at(since))
