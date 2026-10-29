"""Colours, type and fixed labels of the code-made graphics (all numbers live in config/parameters/graphics.json)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache, lru_cache
from pathlib import Path

from PIL import ImageFont, features

from app.core.ai_config import GraphicsConfig, ai_config
from app.documentary.visuals.shaping import RTL

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
RAQM = features.check("raqm")

# role -> font file per script; Newsreader is a variable font (weight, optical size)
FILES = {
    ("latin", "title"): "BarlowCondensed-600.ttf",
    ("latin", "title_medium"): "BarlowCondensed-500.ttf",
    ("latin", "body"): "Newsreader-var.ttf",
    ("latin", "body_bold"): "Newsreader-var.ttf",
    ("latin", "body_italic"): "Newsreader-Italic-var.ttf",
    ("latin", "mono"): "IBMPlexMono-400.ttf",
    ("latin", "mono_bold"): "IBMPlexMono-500.ttf",
    ("fa", "title"): "Vazirmatn-700.ttf",
    ("fa", "title_medium"): "Vazirmatn-700.ttf",
    ("fa", "body"): "Vazirmatn-400.ttf",
    ("fa", "body_bold"): "Vazirmatn-700.ttf",
    ("fa", "body_italic"): "Vazirmatn-400.ttf",
    ("fa", "mono"): "Vazirmatn-400.ttf",
    ("fa", "mono_bold"): "Vazirmatn-700.ttf",
    ("ar", "title"): "NotoNaskhArabic-700.ttf",
    ("ar", "title_medium"): "NotoNaskhArabic-700.ttf",
    ("ar", "body"): "NotoNaskhArabic-400.ttf",
    ("ar", "body_bold"): "NotoNaskhArabic-700.ttf",
    ("ar", "body_italic"): "NotoNaskhArabic-400.ttf",
    ("ar", "mono"): "NotoNaskhArabic-400.ttf",
    ("ar", "mono_bold"): "NotoNaskhArabic-700.ttf",
}
VARIABLE_WEIGHT = {"body": 400, "body_bold": 700, "body_italic": 400}


def config() -> GraphicsConfig:
    return ai_config.graphics_settings()


def is_rtl(language: str) -> bool:
    return language in RTL


THEMED_COLORS = ("ground", "glow", "card", "rule", "amber", "blue", "red")
_ACTIVE: ContextVar[str | None] = ContextVar("graphic_theme", default=None)


@contextmanager
def themed(kind: str | None) -> Iterator[None]:
    """While a card of `kind` is drawn its theme's colours stand behind the colour names its layout uses (a kind with
    no theme, the map for one, keeps the common colours)."""
    token = _ACTIVE.set(kind)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def active() -> str | None:
    """The kind whose theme is active (None outside a themed card)."""
    kind = _ACTIVE.get()
    return kind if kind in config().themes else None


def theme_of(kind: str | None):
    return config().themes.get(kind) if kind else None


@cache
def _hex(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb(name: str) -> tuple[int, int, int]:
    theme = theme_of(active())
    if theme is not None and name in THEMED_COLORS:
        return _hex(getattr(theme, name))
    return _hex(config().colors[name])


@lru_cache(maxsize=256)
def font(language: str, role: str, size: int) -> ImageFont.FreeTypeFont:
    script = language if language in RTL else "latin"
    layout = ImageFont.Layout.RAQM if RAQM else ImageFont.Layout.BASIC
    f = ImageFont.truetype(str(FONT_DIR / FILES[(script, role)]), size, layout_engine=layout)
    if script == "latin" and role in VARIABLE_WEIGHT:
        f.set_variation_by_axes([VARIABLE_WEIGHT[role], min(max(size * 0.5, 6), 72)])
    return f


def size(name: str) -> int:
    return config().type_scale[name]


def label(language: str, key: str) -> str:
    return config().labels[language][key]
