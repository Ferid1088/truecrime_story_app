"""Colours, type and fixed labels of the code-made graphics (all numbers live in config/parameters/graphics.json)."""

from __future__ import annotations

from functools import cache, lru_cache
from pathlib import Path

from PIL import ImageFont, features

from app.core.ai_config import GraphicsConfig, ai_config
from app.documentary.visuals.typography import RTL

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
RAQM = features.check("raqm")

# role -> font file per script; Newsreader is a variable font (weight, optical size)
FILES = {
    ("latin", "title"): "BarlowCondensed-600.ttf",
    ("latin", "title_medium"): "BarlowCondensed-500.ttf",
    ("latin", "body"): "Newsreader-var.ttf",
    ("latin", "body_bold"): "Newsreader-var.ttf",
    ("latin", "mono"): "IBMPlexMono-400.ttf",
    ("latin", "mono_bold"): "IBMPlexMono-500.ttf",
    ("fa", "title"): "Vazirmatn-700.ttf",
    ("fa", "title_medium"): "Vazirmatn-700.ttf",
    ("fa", "body"): "Vazirmatn-400.ttf",
    ("fa", "body_bold"): "Vazirmatn-700.ttf",
    ("fa", "mono"): "Vazirmatn-400.ttf",
    ("fa", "mono_bold"): "Vazirmatn-700.ttf",
    ("ar", "title"): "NotoNaskhArabic-700.ttf",
    ("ar", "title_medium"): "NotoNaskhArabic-700.ttf",
    ("ar", "body"): "NotoNaskhArabic-400.ttf",
    ("ar", "body_bold"): "NotoNaskhArabic-700.ttf",
    ("ar", "mono"): "NotoNaskhArabic-400.ttf",
    ("ar", "mono_bold"): "NotoNaskhArabic-700.ttf",
}
VARIABLE_WEIGHT = {"body": 400, "body_bold": 700}


def config() -> GraphicsConfig:
    return ai_config.graphics_settings()


def is_rtl(language: str) -> bool:
    return language in RTL


@cache
def rgb(name: str) -> tuple[int, int, int]:
    h = config().colors[name].lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


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
