"""The text of a graphic is revealed word by word, in silence, at a pace slower than reading.

A page is drawn twice: with its text and without it (the empty page: frames, cards and lines). Each word the page drew
knows its box (`page.Word`); every pixel of text belongs to the word it lies in (or the nearest one), so a frame is the
empty page plus the words that are visible by then - each fading in - and nothing else. At the end the page is the
finished page, pixel for pixel.

Two kinds of graphic:
- a graphic with its own page (a conflict, a quote, a recap, an engine): its empty page is shown first, then the words
  come in the order they are read, step by step as the planner's cues say;
- a board that continues (`stream.continued`: the timeline, the board of conflicts): what it shows stays, a new card fades
  in and only its new words are streamed.
The schedule (when each step starts, how long the whole graphic needs) follows from the words and `stream.words_per_second`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image

from app.documentary.graphics import theme as T
from app.documentary.graphics.page import text_hidden

TEXT_DIFFERENCE = 12            # a pixel that differs this much between the page with and without text is text


class StreamError(ValueError):
    """The page cannot be streamed (its words differ between the page with and without text)."""


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def ease(x: float) -> float:
    x = clamp01(x)
    return x * x * (3 - 2 * x)


class Layer:
    """One page of a graphic: with its text, without it, and which word every pixel of text belongs to."""

    def __init__(self, graphic, data: dict, language: str, step: int):
        self.full = graphic.draw_state(data, language, step)
        with text_hidden():
            self.bare = graphic.draw_state(data, language, step)
        self.words = tuple(getattr(self.full, "words", ()))
        if len(self.words) != len(getattr(self.bare, "words", ())):
            raise StreamError(f"{graphic.kind} step {step}: the words of the page change when its text is hidden")
        self._full = np.asarray(self.full.convert("RGB"), dtype=np.float32)
        self._bare = np.asarray(self.bare.convert("RGB"), dtype=np.float32)
        self._owner: np.ndarray | None = None

    @property
    def texts(self) -> list[str]:
        return [w.text for w in self.words]

    @property
    def owner(self) -> np.ndarray:
        """For every pixel the index of the word it belongs to (-1: no word)."""
        if self._owner is None:
            height, width = self._full.shape[:2]
            owner = np.full((height, width), -1, np.int32)
            for i, word in enumerate(self.words):
                x0, y0, x1, y1 = word.box
                owner[max(y0, 0):max(min(y1, height), 0), max(x0, 0):max(min(x1, width), 0)] = i
            text = np.abs(self._full - self._bare).max(axis=2) > TEXT_DIFFERENCE
            stray = text & (owner < 0)
            seeds = text & (owner >= 0)
            if stray.any() and seeds.any():        # text outside every box goes with the nearest word
                source = np.where(seeds, 0, 255).astype(np.uint8)
                _, labels = cv2.distanceTransformWithLabels(source, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
                seed_owner = owner.ravel()[np.flatnonzero(seeds.ravel())]
                owner[stray] = seed_owner[labels[stray] - 1]
            self._owner = owner
        return self._owner

    def compose(self, visible: np.ndarray) -> Image.Image:
        """The page with word i drawn at strength visible[i] (0..1)."""
        if len(visible) == 0 or float(visible.min()) >= 1.0:
            return self.full
        if float(visible.max()) <= 0.0:
            return self.bare
        strength = np.concatenate([[1.0], visible]).astype(np.float32)[self.owner + 1]
        out = self._bare + (self._full - self._bare) * strength[..., None]
        return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


def carried_over(data: dict) -> bool:
    """True for a card that continues an earlier one (`shown` points are already on the line): what its first page holds
    was seen on the earlier card, so it is on screen from the start and is not streamed again."""
    return int(data.get("shown") or 0) > 0


def new_words(layers: list[Layer], continued: bool, carried: bool = False) -> list[list[int]]:
    """For each step the indices of the words that appear then: of the page shown at that step (a continuing board) or of
    the last page (a page of its own, whose words come in step by step). A word belongs to the first step whose page
    holds it."""
    if continued:
        out = [[] if carried else list(range(len(layers[0].words)))]
        for before, after in zip(layers, layers[1:], strict=False):
            matched = set()
            for block in SequenceMatcher(None, before.texts, after.texts, autojunk=False).get_matching_blocks():
                matched.update(range(block.b, block.b + block.size))
            out.append([i for i in range(len(after.words)) if i not in matched])
        return out
    last = layers[-1]
    first = [len(layers) - 1] * len(last.words)
    for step in range(len(layers) - 2, -1, -1):
        for block in SequenceMatcher(None, layers[step].texts, last.texts, autojunk=False).get_matching_blocks():
            for j in range(block.b, block.b + block.size):
                first[j] = step
    return [[i for i, s in enumerate(first) if s == step] for step in range(len(layers))]


def is_continued(kind: str) -> bool:
    return kind in T.config().stream.continued


def lead_of(kind: str) -> float:
    cfg = T.config().stream
    return cfg.appear_seconds if is_continued(kind) else cfg.lead_seconds


@dataclass(frozen=True)
class Schedule:
    """When the steps of a graphic start (cues, seconds into the shot) and how long the whole graphic needs at the
    streaming pace, with its end hold; `counts` are the words that come in at each step (step 0 = the first page)."""

    cues: tuple[float, ...]
    total: float
    counts: tuple[int, ...]


@lru_cache(maxsize=64)
def schedule(kind: str, data_json: str, language: str) -> Schedule:
    from app.documentary.graphics.registry import graphic

    g, data = graphic(kind), json.loads(data_json)
    layers = [Layer(g, data, language, step) for step in range(g.required_cues(data) + 1)]
    counts = tuple(len(ix) for ix in new_words(layers, is_continued(kind), carried_over(data)))
    cfg = T.config().stream
    pace = 1.0 / cfg.words_per_second
    t = cfg.start_seconds + counts[0] * pace
    cues = []
    for count in counts[1:]:
        t += cfg.pause_seconds if t > cfg.start_seconds else 0.0
        cues.append(round(t, 3))
        t += lead_of(kind) + count * pace
    return Schedule(tuple(cues), round(t + cfg.end_hold_seconds, 3), counts)


class Stream:
    """The frames of one graphic over time, from its cues: the pages are drawn once, a frame only composes."""

    def __init__(self, graphic, data: dict, language: str, cues: list[float], duration: float, rest: float):
        self.kind, self.cues, self.duration = graphic.kind, list(cues), duration
        self.continued = is_continued(graphic.kind)
        self.cfg = T.config().stream
        self.layers = [Layer(graphic, data, language, step) for step in range(len(self.cues) + 1)]
        self.new = new_words(self.layers, self.continued, carried_over(data))
        self.limit_end = duration - rest
        self._times = self._word_times()

    def _window(self, step: int) -> tuple[float, float]:
        """(start, length) of the streaming of the words that come in at `step`."""
        count = len(self.new[step])
        if step == 0:
            limit = self.cues[0] if self.cues else self.limit_end
            start = min(self.cfg.start_seconds, 0.4 * limit)
        else:
            start = self.cues[step - 1] + lead_of(self.kind)
            limit = self.cues[step] if step < len(self.cues) else self.limit_end
        available = max(limit - start - 0.2, 0.3)
        return start, min(count / self.cfg.words_per_second, available)

    def _word_times(self) -> list[np.ndarray]:
        """Per step, the time at which each of its new words starts to appear."""
        out = []
        for step, words in enumerate(self.new):
            start, length = self._window(step)
            out.append(start + np.arange(len(words)) * (length / max(len(words), 1)))
        return out

    def _fade(self, step: int) -> float:
        start, length = self._window(step)
        return max(min(self.cfg.word_fade_seconds, 3 * length / max(len(self.new[step]), 1)), 1e-3)

    def _visible(self, layer: Layer, t: float, steps: range, known: set[int]) -> np.ndarray:
        vis = np.zeros(len(layer.words), np.float32)
        vis[list(known)] = 1.0
        for step in steps:
            fade = self._fade(step)
            for m, i in enumerate(self.new[step]):
                vis[i] = clamp01((t - self._times[step][m]) / fade)
        return vis

    def words_shown(self, since: float) -> float:
        """How many words of the page on screen are drawn by now (a fading word counts by its strength)."""
        step = sum(1 for c in self.cues if since >= c)
        if not self.continued:
            layer = self.layers[-1]
            return float(self._visible(layer, since, range(len(self.layers)), set()).sum())
        layer = self.layers[step]
        known = set(range(len(layer.words))) - set(self.new[step])
        return float(self._visible(layer, since, range(step, step + 1), known).sum())

    def image(self, since: float) -> Image.Image:
        step = sum(1 for c in self.cues if since >= c)
        if not self.continued:
            layer = self.layers[-1]
            return layer.compose(self._visible(layer, since, range(len(self.layers)), set()))
        layer = self.layers[step]
        known = set(range(len(layer.words))) - set(self.new[step])
        if step > 0 and since < self.cues[step - 1] + lead_of(self.kind):      # the new card comes in
            k = ease((since - self.cues[step - 1]) / max(lead_of(self.kind), 1e-3))
            held = layer.compose(self._visible(layer, since, range(0), known))
            return Image.blend(self.layers[step - 1].full.convert("RGB"), held.convert("RGB"), k)
        return layer.compose(self._visible(layer, since, range(step, step + 1), known))
