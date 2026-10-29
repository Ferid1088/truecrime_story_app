"""One 1920x1080 page being drawn. Layouts are written once in left-to-right design coordinates; for Persian
and Arabic every box is mirrored, text starts at the right edge and shaping is right-to-left."""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache, wraps

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.documentary.graphics import theme as T
from app.documentary.visuals import shaping

Color = tuple[int, int, int]


@dataclass(frozen=True)
class Word:
    """One word as drawn on a page: its text and the box it covers (page pixels). The words of a page, in drawing order,
    are what the stream reveals one by one."""

    text: str
    box: tuple[int, int, int, int]


WORD_PAD = 6
_HIDDEN: ContextVar[bool] = ContextVar("graphics_text_hidden", default=False)


@contextmanager
def text_hidden():
    """Pages drawn inside this block lay out and record their words but draw no text: the empty page (its frames, cards
    and lines) that the stream shows first."""
    token = _HIDDEN.set(True)
    try:
        yield
    finally:
        _HIDDEN.reset(token)


def mode_cache(maxsize: int) -> Callable:
    """lru_cache for a page-drawing function that keeps the text-hidden pages apart from the pages with text."""
    def decorate(fn: Callable) -> Callable:
        cached = lru_cache(maxsize=maxsize)(lambda hidden, theme, *args, **kw: fn(*args, **kw))

        @wraps(fn)
        def wrapper(*args, **kw):
            return cached(_HIDDEN.get(), T.active(), *args, **kw)

        wrapper.cache_clear = cached.cache_clear
        return wrapper
    return decorate


def rotated_words(words: tuple[Word, ...], size: tuple[int, int], degrees: float, offset: tuple[int, int]
                  ) -> list[Word]:
    """The words of a sheet that is rotated by `degrees` (PIL's rotate(expand=True), counter-clockwise) and laid on a page
    at `offset`: each box becomes the box around its rotated corners."""
    w, h = size
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    rw, rh = abs(w * cos) + abs(h * sin), abs(w * sin) + abs(h * cos)
    out = []
    for word in words:
        xs, ys = [], []
        for x, y in ((word.box[0], word.box[1]), (word.box[2], word.box[1]), (word.box[0], word.box[3]),
                     (word.box[2], word.box[3])):
            vx, vy = x - w / 2, y - h / 2
            xs.append(rw / 2 + vx * cos + vy * sin + offset[0])
            ys.append(rh / 2 - vx * sin + vy * cos + offset[1])
        out.append(Word(word.text, (int(min(xs)), int(min(ys)), math.ceil(max(xs)), math.ceil(max(ys)))))
    return out


class DoesNotFit(ValueError):
    """The text cannot be set inside its box even at the smallest allowed size."""


@lru_cache(maxsize=64)
def ground(width: int, height: int, theme: str | None = None, rtl: bool = False) -> Image.Image:
    """The page ground: a near-black tint with a soft light from the top left, in the card kind's own colours when it
    has a theme, with the kind's frame drawn on it."""
    from app.documentary.graphics import frames

    with T.themed(theme):
        base = np.array(T.rgb("ground"), np.float32)
        glow = np.array(T.rgb("glow"), np.float32)
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
        d = np.sqrt((xx / (width * 0.75)) ** 2 + (yy / (height * 0.95)) ** 2)
        k = np.clip(1.0 - d, 0.0, 1.0)[..., None] ** 1.6
        img = Image.fromarray((base + (glow - base) * k).astype(np.uint8), "RGB")
        spec = T.theme_of(T.active())
        if spec is None:
            return img
        return frames.draw(img, spec.frame, T.rgb("amber"), T.rgb("rule"), rtl)


class Page:
    def __init__(self, language: str, size: tuple[int, int] | None = None, fill: Color | None = None):
        """The film's page (ground and soft light), or a plain layer of `size` filled with `fill` (a paper card)."""
        canvas = T.config().canvas
        self.language, self.rtl = language, T.is_rtl(language)
        self.W, self.H = size or (canvas["width"], canvas["height"])
        self.img = (Image.new("RGBA", (self.W, self.H), (*fill, 255)) if fill is not None
                    else ground(self.W, self.H, T.active(), self.rtl).copy().convert("RGBA"))
        self.d = ImageDraw.Draw(self.img)
        self.margin = T.config().margin
        self.words: list[Word] = []              # every word drawn on the page, in drawing order

    @staticmethod
    def _px(role: str, px: int) -> int:
        """Label text (the mono roles) is never set smaller than the type scale's `min_label`: it has to be read."""
        return max(px, T.size("min_label")) if role.startswith("mono") else px

    # -- geometry ------------------------------------------------------
    def x(self, left: float, width: float = 0) -> int:
        """Actual left edge of a box laid out at `left` (design coordinates)."""
        return int(self.W - left - width if self.rtl else left)

    def width_inside(self) -> int:
        return self.W - 2 * self.margin

    # -- shapes --------------------------------------------------------
    def rect(self, left, top, width, height, fill=None, outline=None, line=1, alpha=255) -> None:
        box = (self.x(left, width), top, self.x(left, width) + width, top + height)
        if alpha < 255 and fill is not None:
            layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).rectangle(box, fill=(*fill, alpha))
            self.img.alpha_composite(layer)
            self.d = ImageDraw.Draw(self.img)
            fill = None
        if fill is not None or outline is not None:
            self.d.rectangle(box, fill=fill, outline=outline, width=line)

    def dashed_rect(self, left, top, width, height, color: Color, dash=14, gap=10, line=3) -> None:
        x0, y0 = self.x(left, width), top
        x1, y1 = x0 + width, y0 + height
        x = x0
        while x < x1:
            self.d.line((x, y0, min(x + dash, x1), y0), fill=color, width=line)
            self.d.line((x, y1, min(x + dash, x1), y1), fill=color, width=line)
            x += dash + gap
        y = y0
        while y < y1:
            self.d.line((x0, y, x0, min(y + dash, y1)), fill=color, width=line)
            self.d.line((x1, y, x1, min(y + dash, y1)), fill=color, width=line)
            y += dash + gap

    def disc(self, cx: float, cy: float, radius: float, fill: Color, halo: float = 0.0, halo_alpha: int = 70) -> None:
        cx = self.W - cx if self.rtl else cx
        if halo:
            layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).ellipse((cx - halo, cy - halo, cx + halo, cy + halo), fill=(*fill, halo_alpha))
            self.img.alpha_composite(layer)
            self.d = ImageDraw.Draw(self.img)
        self.d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)

    def ring(self, cx: float, cy: float, radius: float, color: Color, line: int = 4) -> None:
        cx = self.W - cx if self.rtl else cx
        self.d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=line)

    def hline(self, left, top, width, color: Color, line: int = 2) -> None:
        a = self.x(left, width)
        self.d.line((a, top, a + width, top), fill=color, width=line)

    def vline(self, left, top, height, color: Color, line: int = 2) -> None:
        a = self.x(left)
        self.d.line((a, top, a, top + height), fill=color, width=line)

    def shadow_under(self, left, top, width, height, blur=26, offset=(0, 18), alpha=150) -> None:
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        a = self.x(left, width)
        ImageDraw.Draw(layer).rectangle((a + offset[0], top + offset[1], a + width + offset[0],
                                         top + height + offset[1]), fill=(0, 0, 0, alpha))
        self.img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))
        self.d = ImageDraw.Draw(self.img)

    # -- text ----------------------------------------------------------
    def lines(self, text: str, role: str, px: int, width: int, lines_max: int | None = None,
              min_px: int | None = None) -> tuple[list[str], int]:
        """Wrap `text` into `width`, shrinking from `px` toward `min_px` until it fits `lines_max` lines."""
        px = self._px(role, px)
        min_px = self._px(role, min_px) if min_px else min_px
        text = " ".join(text.split())
        floor = min_px or px
        for p in range(px, floor - 1, -2):
            f = T.font(self.language, role, p)
            wrapped = shaping.wrap(text, f, self.language, width)
            fits = all(shaping.width(line, f, self.language) <= width for line in wrapped)
            if fits and (lines_max is None or len(wrapped) <= lines_max):
                return wrapped, p
        raise DoesNotFit(f"'{text[:40]}…' needs more than {lines_max} lines at {floor}px")

    def text(self, left: float, top: float, width: int, text: str, role: str, px: int, fill: Color,
             align: str = "start", lines_max: int | None = None, min_px: int | None = None,
             spacing: float = 1.18, upper: bool = False, alpha: int = 255) -> int:
        """Draw wrapped text in the box (left, top, width) and return the y below it."""
        px = self._px(role, px)
        if upper and not self.rtl:
            text = text.upper()
        lines, used = self.lines(text, role, px, width, lines_max, min_px)
        f = T.font(self.language, role, used)
        step = int(used * spacing * (1.22 if self.rtl else 1.0))
        box = self.x(left, width)
        y = top
        layer = self.img if alpha >= 255 else Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        draw = self.d if alpha >= 255 else ImageDraw.Draw(layer)
        kw = shaping.kwargs(self.language)
        hidden = _HIDDEN.get()
        for line in lines:
            w = shaping.width(line, f, self.language)
            if align == "center":
                px_ = box + (width - w) / 2
            elif (align == "start" and self.rtl) or (align == "end" and not self.rtl):
                px_ = box + width - w
            else:
                px_ = box
            self._record(line, px_, y, w, step, f)
            if not hidden:
                draw.text((px_, y), shaping.display(line, self.language), font=f, fill=(*fill, alpha), **kw)
            y += step
        if alpha < 255:
            self.img.alpha_composite(layer)
            self.d = ImageDraw.Draw(self.img)
        return y

    def _record(self, line: str, left: float, top: float, width: float, step: int, font) -> None:
        """Note each word of a drawn line with the box it covers (its right end is measured from the logical start of
        the line, so right-to-left lines work the same way)."""
        words = line.split(" ")
        for j, word in enumerate(words):
            if not word:
                continue
            end = shaping.width(" ".join(words[:j + 1]), font, self.language)
            own = shaping.width(word, font, self.language)
            x1 = left + width - (end - own) if self.rtl else left + end
            x0 = x1 - own
            self.words.append(Word(word, (int(x0) - WORD_PAD, int(top), math.ceil(x1) + WORD_PAD, int(top + step))))

    def put(self, x: float, top: float, text: str, font, fill: Color, width: float) -> None:
        """One already-placed word (the redacted context lines of a document draw word by word)."""
        self.words.append(Word(text, (int(x) - WORD_PAD, int(top), math.ceil(x + width) + WORD_PAD,
                                      int(top + font.size * 1.4))))
        if not _HIDDEN.get():
            self.d.text((x, top), text, font=font, fill=fill)

    def tracked(self, left: float, top: float, text: str, role: str, px: int, fill: Color, tracking: int = 4,
                align: str = "start", width: int = 0) -> int:
        """A short label with letter-spacing (Latin only; right-to-left scripts are never spaced out)."""
        px = self._px(role, px)
        f = T.font(self.language, role, px)
        if self.rtl or tracking == 0:
            return self.text(left, top, width or self.W, text, role, px, fill, align=align, lines_max=1)
        w = sum(shaping.width(c, f, self.language) + tracking for c in text) - tracking
        start = self.x(left, width or w) + ((width - w) / 2 if align == "center" and width else 0)
        x, word_from, word = start, start, ""
        hidden = _HIDDEN.get()
        for c in text + " ":
            if c == " ":
                if word:
                    self.words.append(Word(word, (int(word_from) - WORD_PAD, int(top), math.ceil(x) + WORD_PAD,
                                                  int(top + px * 1.3))))
                word = ""
                x += (shaping.width(" ", f, self.language) + tracking) if c == " " else 0
                word_from = x
                continue
            if not hidden:
                self.d.text((x, top), c, font=f, fill=fill, **shaping.kwargs(self.language))
            word += c
            x += shaping.width(c, f, self.language) + tracking
        return top + int(px * 1.3)

    def measure(self, text: str, role: str, px: int) -> int:
        return shaping.width(text, T.font(self.language, role, self._px(role, px)), self.language)

    def done(self) -> Image.Image:
        """The finished page; its `words` are the words drawn on it, in order."""
        out = self.img.convert("RGB")
        out.words = tuple(self.words)
        return out
