"""Full-frame cards the renderer draws from the production script — no
editorial logic here: what a card says and where the timeline stands was
decided (and approved) before render.

  chapter  — "Chapter 2" (small, spaced) over a red rule, the chapter's
             title in serif; only the number when its title was left out
  title    — the film's title after the cold open
  timeline — the running case timeline: the dates the viewer knows as
             ticks on a line, the marker sliding to the current date, the
             date and what happened above it (time runs left to right in
             every language; each year labelled once)

Cards sit on a near-black ground with a soft vignette and move very
slightly (a slow 3 % push), so a held card never looks frozen.
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.core.ai_config import ai_config
from app.documentary.visuals.typography import (
    RTL,
    _draw_lines,
    _kw,
    font,
    text_width,
    wrap,
)

INK = (245, 242, 235)
MUTED = (138, 134, 128)
DIM = (62, 60, 58)
RED = (206, 44, 40)
CARD_KINDS = ("chapter", "title", "timeline")


def _ease(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return p * p * (3 - 2 * p)


@lru_cache(maxsize=4)
def ground(W: int, H: int) -> np.ndarray:
    """Near-black with a faint warm centre and darker corners."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    v = np.clip(1.0 - 0.55 * d, 0.0, 1.0)[..., None]
    lo, hi = np.array([5, 5, 6], np.float32), np.array([24, 22, 21], np.float32)
    return (lo + (hi - lo) * v).astype(np.uint8)


def _canvas(W: int, H: int) -> Image.Image:
    return Image.fromarray(ground(W, H).copy(), "RGB")


def chapter_image(label: str, title: str | None, language: str, W: int, H: int) -> np.ndarray:
    img = _canvas(W, H)
    d = ImageDraw.Draw(img)
    s = H / 1080
    small = font(language, "sans", "bold", int(30 * s))
    big = font(language, "serif", "bold", int(78 * s))
    lab = label if language in RTL else " ".join(label.upper())  # spaced capitals
    lines = wrap(title, big, language, int(W * 0.72))[:3] if title else []
    lh = int(big.size * 1.22)
    block = int(small.size * 1.4) + int(34 * s) + lh * len(lines)
    y = (H - block) // 2
    lw = text_width(lab, small, language)
    d.text(((W - lw) / 2, y), lab, font=small, fill=MUTED, **_kw(language))
    y += int(small.size * 1.4) + int(14 * s)
    rule = int(64 * s)
    d.rectangle(((W - rule) // 2, y, (W + rule) // 2, y + max(3, int(4 * s))), fill=RED)
    y += int(20 * s)
    if lines:
        _draw_lines(d, lines, big, language, int(W * 0.14), y, int(W * 0.72), "center", INK, 1.22)
    return np.asarray(img)


def title_image(title: str, language: str, W: int, H: int) -> np.ndarray:
    img = _canvas(W, H)
    d = ImageDraw.Draw(img)
    s = H / 1080
    big = font(language, "serif", "bold", int(96 * s))
    lines = wrap(title, big, language, int(W * 0.78))[:3]
    lh = int(big.size * 1.2)
    y = (H - lh * len(lines)) // 2 - int(20 * s)
    _draw_lines(d, lines, big, language, int(W * 0.11), y, int(W * 0.78), "center", INK, 1.2)
    rule = int(90 * s)
    ry = y + lh * len(lines) + int(26 * s)
    d.rectangle(((W - rule) // 2, ry, (W + rule) // 2, ry + max(3, int(4 * s))), fill=RED)
    return np.asarray(img)


def push(frame: np.ndarray, p: float, amount: float = 0.03) -> np.ndarray:
    """A slow push into the card (p 0..1 over the card)."""
    z = 1.0 + amount * _ease(p)
    H, W = frame.shape[:2]
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, z)
    return cv2.warpAffine(frame, M, (W, H), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


class TimelineCard:
    """The running timeline of one shot: a static base (line, ticks, year
    labels, date and label) and a marker that slides to the current date."""

    def __init__(self, shot: dict, language: str, W: int, H: int):
        self.W, self.H = W, H
        tl = shot.get("timeline") or {}
        s = self.s = H / 1080
        self.y = int(H * 0.64)
        self.x0, self.x1 = int(W * 0.1), int(W * 0.9)
        self.events = tl.get("events") or []
        self.from_x = self._px(float(tl.get("from_x") or 0.0))
        self.to_x = self._px(float(tl.get("to_x") or 0.5))
        self.move = ai_config.chapters.timeline_move_seconds
        img = _canvas(W, H)
        d = ImageDraw.Draw(img)
        lw = max(2, int(3 * s))
        d.line((self.x0, self.y, self.x1, self.y), fill=DIM, width=lw)
        r = int(7 * s)
        yf = font(language, "sans", "regular", int(26 * s))
        for e in self.events:
            if not e.get("current"):
                x = self._px(float(e["x"]))
                d.ellipse((x - r, self.y - r, x + r, self.y + r), fill=MUTED)
        # each label once (a year under its group of ticks, or day and
        # month inside one year); the current one first, others where they fit
        labels = tl.get("labels")
        if labels is None:  # (scripts composed before labels existed)
            seen: dict[str, dict] = {}
            for e in self.events:
                if e.get("year"):
                    seen.setdefault(str(e["year"]), {"text": str(e["year"]), "x": e["x"],
                                                     "current": False})
                    seen[str(e["year"])]["current"] |= bool(e.get("current"))
            labels = list(seen.values())
        drawn: list[tuple[float, float]] = []
        for lb in sorted(labels, key=lambda lb: not lb.get("current")):
            x = self._px(float(lb["x"]))
            text = str(lb["text"])
            w = text_width(text, yf, language)
            a, b = x - w / 2 - 8 * s, x + w / 2 + 8 * s
            if lb.get("current") or not any(a < q and b > p for p, q in drawn):
                d.text((x - w / 2, self.y + int(26 * s)), text, font=yf,
                       fill=INK if lb.get("current") else MUTED, **_kw(language))
                drawn.append((a, b))
        self.base = np.asarray(img).copy()
        # the date and what happened, centred above the line
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        df = font(language, "sans", "bold", int(64 * s))
        lf = font(language, "serif", "regular", int(46 * s))
        label = tl.get("label")
        lines = wrap(label, lf, language, int(W * 0.7))[:2] if label else []
        lh = int(lf.size * 1.3)
        top = self.y - int(120 * s) - lh * len(lines) - int(df.size * 1.2)
        date = str(tl.get("date") or "")
        w = text_width(date, df, language)
        dl.text(((W - w) / 2, top), date, font=df, fill=INK + (255,), **_kw(language))
        if lines:
            _draw_lines(dl, lines, lf, language, int(W * 0.15), top + int(df.size * 1.35),
                        int(W * 0.7), "center", (225, 221, 214, 255), 1.3)
        arr = np.asarray(layer).astype(np.float32)
        self.text_rgb, self.text_a = arr[..., :3], arr[..., 3:] / 255.0

    def _px(self, x: float) -> int:
        # time runs left to right in every language (also Persian/Arabic)
        return int(self.x0 + (self.x1 - self.x0) * max(0.0, min(1.0, x)))

    def frame(self, since: float, p: float) -> np.ndarray:
        s = self.s
        out = self.base.copy()
        m = _ease(since / self.move)
        x = int(self.from_x + (self.to_x - self.from_x) * m)
        start = self.x0
        cv2.line(out, (start, self.y), (x, self.y), (200, 196, 190), max(2, int(3 * s)),
                 cv2.LINE_AA)
        cv2.line(out, (x, self.y - int(46 * s)), (x, self.y - int(14 * s)), RED,
                 max(2, int(2 * s)), cv2.LINE_AA)
        cv2.circle(out, (x, self.y), int(18 * s), (70, 24, 22), -1, cv2.LINE_AA)
        cv2.circle(out, (x, self.y), int(11 * s), RED, -1, cv2.LINE_AA)
        a = _ease((since - 0.3) / 0.6) * self.text_a
        out = (out.astype(np.float32) * (1 - a) + self.text_rgb * a).astype(np.uint8)
        return push(out, p, 0.02)
