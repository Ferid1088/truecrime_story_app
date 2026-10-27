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
from app.documentary.graphics.page import DoesNotFit, ground
from app.documentary.graphics.streaming import Stream, StreamError


def lowest_content_row(img: Image.Image, tolerance: int) -> int:
    """The lowest pixel row of the page that differs from the page ground (-1 when the page is empty)."""
    page = np.asarray(img.convert("RGB"), dtype=np.int16)
    base = np.asarray(ground(img.width, img.height).convert("RGB"), dtype=np.int16)
    rows = np.where((np.abs(page - base).max(axis=2) > tolerance).any(axis=1))[0]
    return int(rows.max()) if len(rows) else -1


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
    #: whether the text of the graphic is revealed word by word (streaming.py)
    streamable = True

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
                rendered.raw(step)
        except DoesNotFit as e:
            raise GraphicUnavailable(f"{self.kind}: {e}") from e
        if rendered.scale() < T.config().safe_zone["min_scale"]:
            raise GraphicUnavailable(f"{self.kind}: its content reaches too far into the subtitle zone at the "
                                     f"bottom of the page")
        rendered.stream()                              # the pages with and without text are drawn now as well
        return rendered


class Rendered:
    """A graphic fitted to a duration and cues: asks for the frame at any time since its start."""

    def __init__(self, graphic: Graphic, data: dict, language: str, duration_s: float, cues: list[float],
                 skipped: list[str]):
        self.graphic, self.data, self.language = graphic, data, language
        self.duration_s, self.cues, self.skipped = duration_s, cues, skipped
        self.steps = len(cues)
        self._raw: dict[int, Image.Image] = {}
        self._scale: float | None = None
        self._stream: Stream | None = None
        self._stream_failed = False

    def key(self) -> str:
        return hashlib.sha256(json.dumps([self.graphic.kind, self.data, self.language, self.cues],
                                         sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def raw(self, step: int) -> Image.Image:
        """The page as the graphic draws it, before it is fitted above the subtitle zone."""
        if step not in self._raw:
            self._raw[step] = self.graphic.draw_state(self.data, self.language, step)
        return self._raw[step]

    def scale(self) -> float:
        """The one factor all pages of this graphic are shrunk by so that nothing reaches into the strip at the bottom
        that the subtitles use (1.0 when nothing does). It comes from the lowest content of any page, so the pages
        of one graphic never move against each other."""
        if self._scale is None:
            zone = T.config().safe_zone
            lowest = max(lowest_content_row(self.raw(step), zone["tolerance"]) for step in range(self.steps + 1))
            limit = self.raw(0).height - zone["bottom"]
            self._scale = 1.0 if lowest < 0 or lowest + 1 <= limit else limit / (lowest + 1)
        return self._scale

    def fitted(self, img: Image.Image) -> Image.Image:
        """The page shrunk by `scale()`, centred at the top over the page ground."""
        factor = self.scale()
        if factor >= 1.0:
            return img
        size = (round(img.width * factor), round(img.height * factor))
        canvas = ground(img.width, img.height).copy()
        canvas.paste(img.resize(size, Image.LANCZOS), ((img.width - size[0]) // 2, 0))
        return canvas

    def state(self, step: int) -> Image.Image:
        return self.fitted(self.raw(step))

    def position(self, since: float) -> tuple[int, float]:
        """(step, progress of its reveal): step k is on screen once cue k-1 has passed."""
        step = sum(1 for c in self.cues if since >= c)
        if step == 0:
            return 0, 1.0
        return step, clamp01((since - self.cues[step - 1]) / T.config().transition_seconds)

    def stream(self) -> Stream | None:
        """The word-by-word reveal of this graphic (None when it is switched off or the page cannot be streamed)."""
        if not (T.config().stream.enabled and self.graphic.streamable) or self._stream_failed:
            return None
        if self._stream is None:
            try:
                self._stream = Stream(self.graphic, self.data, self.language, self.cues, self.duration_s,
                                      self.graphic.minimum_rest_seconds)
            except StreamError:
                self._stream_failed = True
                return None
        return self._stream

    def image_at(self, since: float) -> Image.Image:
        stream = self.stream()
        if stream is not None:
            return self.fitted(stream.image(since))
        step, progress = self.position(since)
        if step == 0 or progress >= 1.0:
            return self.state(step)
        return self.fitted(self.graphic.draw_between(self.data, self.language, step, progress, self.raw(step - 1),
                                                     self.raw(step)))

    def frame_at(self, since: float) -> np.ndarray:
        return np.asarray(self.image_at(since))
