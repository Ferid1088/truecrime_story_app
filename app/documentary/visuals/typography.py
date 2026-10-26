"""Typography for on-screen text in four languages (Latin and
right-to-left Persian/Arabic with proper shaping via Pillow's raqm
layout). Fonts are bundled (SIL Open Font License, see assets/fonts).

Everything here returns RGBA layers at frame size; the renderer only
composites them. Kinds:
  date / place  — small lower-left label (place with a marker dot)
  quote         — a short real quotation, centred, serif
  title         — case title card
  credit        — tiny attribution, lower right
  subtitle      — burned-in caption, bottom centre
  status        — the case-status stamp ("UNSOLVED CASE"): a near-black
                  box with a red rule, bold text, upper start corner
                  (left for Latin, right for RTL) — readable over any
                  picture, never over the subtitles or the place label
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.documentary.visuals import shaping
from app.documentary.visuals.shaping import RAQM, RTL

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

FONTS = {
    ("latin", "serif", "regular"): "SourceSerif4-400.ttf",
    ("latin", "serif", "bold"): "SourceSerif4-600.ttf",
    ("latin", "sans", "regular"): "Inter-400.ttf",
    ("latin", "sans", "bold"): "Inter-600.ttf",
    ("fa", "serif", "regular"): "Vazirmatn-400.ttf",
    ("fa", "serif", "bold"): "Vazirmatn-700.ttf",
    ("fa", "sans", "regular"): "Vazirmatn-400.ttf",
    ("fa", "sans", "bold"): "Vazirmatn-700.ttf",
    ("ar", "serif", "regular"): "NotoNaskhArabic-400.ttf",
    ("ar", "serif", "bold"): "NotoNaskhArabic-700.ttf",
    ("ar", "sans", "regular"): "NotoNaskhArabic-400.ttf",
    ("ar", "sans", "bold"): "NotoNaskhArabic-700.ttf",
    ("latin", "typewriter", "regular"): "SpecialElite-400.ttf",
}


@lru_cache(maxsize=64)
def font(language: str, family: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    script = language if language in RTL else "latin"
    name = FONTS.get((script, family, weight)) or FONTS[(script, "sans", "regular")]
    layout = ImageFont.Layout.RAQM if RAQM else ImageFont.Layout.BASIC
    return ImageFont.truetype(str(FONT_DIR / name), size, layout_engine=layout)


text_width = shaping.width
wrap = shaping.wrap


def draw_text(draw: ImageDraw.ImageDraw, xy, text: str, f: ImageFont.FreeTypeFont, language: str, **kwargs) -> None:
    """Draw one line of text in its language: shaped and ordered right-to-left for Persian/Arabic with or without raqm."""
    draw.text(xy, shaping.display(text, language), font=f, **shaping.kwargs(language), **kwargs)


def _draw_lines(draw, lines, f, language, box_x, y, width, align, fill, spacing):
    for line in lines:
        w = text_width(line, f, language)
        if align == "center":
            x = box_x + (width - w) / 2
        elif (align == "start" and language in RTL) or align == "end":
            x = box_x + width - w
        else:
            x = box_x
        draw_text(draw, (x, y), line, f, language, fill=fill)
        y += int(f.size * spacing)
    return y


def _shadowed(layer: Image.Image, radius: int = 6, opacity: int = 170) -> Image.Image:
    alpha = layer.split()[-1]
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: min(opacity, a)).filter(ImageFilter.GaussianBlur(radius)))
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (2, 3))
    out.alpha_composite(layer)
    return out


def overlay(kind: str, text: str, language: str, W: int, H: int) -> Image.Image:
    """Frame-size RGBA layer for one piece of on-screen text."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    s = H / 1080
    margin = int(96 * s)
    rtl = language in RTL
    if kind in ("date", "place"):
        f = font(language, "sans", "bold", int(34 * s))
        lines = wrap(text, f, language, int(W * 0.45))
        y = H - margin - int(len(lines) * f.size * 1.3)
        x0 = margin + (int(30 * s) if kind == "place" else 0)
        width = int(W * 0.45)
        if rtl:
            x0 = W - margin - width - (int(30 * s) if kind == "place" else 0)
        if kind == "place":
            cx = (W - margin - int(10 * s)) if rtl else margin + int(10 * s)
            cy = y + int(f.size * 0.6)
            r = int(8 * s)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(214, 64, 52, 255))
        else:
            lx = (W - margin - int(60 * s)) if rtl else margin
            d.line((lx, y - int(14 * s), lx + int(60 * s), y - int(14 * s)),
                   fill=(255, 255, 255, 220), width=max(2, int(3 * s)))
        _draw_lines(d, lines, f, language, x0, y, width, "start",
                    (245, 242, 235, 255), 1.3)
        return _shadowed(layer)
    if kind == "title":
        f = font(language, "serif", "bold", int(76 * s))
        width = int(W * 0.8)
        lines = wrap(text, f, language, width)
        y = (H - int(len(lines) * f.size * 1.25)) // 2
        _draw_lines(d, lines, f, language, (W - width) // 2, y, width, "center",
                    (245, 242, 235, 255), 1.25)
        return layer
    if kind == "label":
        f = font(language, "sans", "regular", int(24 * s))
        w = text_width(text, f, language)
        x = W - margin // 2 - w if rtl else margin // 2
        draw_text(d, (x, int(36 * s)), text, f, language, fill=(235, 235, 235, 190))
        return _shadowed(layer, 3, 150)
    if kind == "credit":
        f = font("en", "sans", "regular", int(20 * s))
        w = text_width(text, f, "en")
        d.text((W - margin // 2 - w, H - int(40 * s)), text, font=f,
               fill=(235, 235, 235, 170))
        return _shadowed(layer, 3, 140)
    if kind == "status":
        return _status_stamp(layer, d, text, language, W, H, s, margin)
    if kind == "subtitle":
        f = font(language, "sans", "regular", int(40 * s))
        width = int(W * 0.8)
        lines = wrap(text, f, language, width)[:2]
        lh = int(f.size * 1.3)
        y = H - int(70 * s) - lh * len(lines)
        pad = int(14 * s)
        widths = [text_width(line, f, language) for line in lines]
        bw = max(widths) + 2 * pad
        d.rounded_rectangle(((W - bw) // 2, y - pad, (W + bw) // 2, y + lh * len(lines) + pad // 2),
                            radius=int(8 * s), fill=(0, 0, 0, 150))
        _draw_lines(d, lines, f, language, (W - width) // 2, y, width, "center",
                    (255, 255, 255, 255), 1.3)
        return layer
    raise ValueError(f"unknown overlay kind {kind!r}")


# Status stamp: high contrast on any picture (near-black box, warm white
# text, a red rule on the reading-start edge and a thin red frame).
STATUS_BOX = (10, 10, 12, 225)
STATUS_RED = (206, 44, 40, 255)
STATUS_TEXT = (248, 245, 238, 255)


def _status_stamp(layer: Image.Image, d: ImageDraw.ImageDraw, text: str, language: str,
                  W: int, H: int, s: float, margin: int) -> Image.Image:
    rtl = language in RTL
    f = font(language, "sans", "bold", int(44 * s))
    lines = wrap(" ".join(text.split()), f, language, int(W * 0.42))[:2]
    lh = int(f.size * 1.3)
    pad_x, pad_y, rule = int(28 * s), int(16 * s), max(4, int(10 * s))
    tw = max(text_width(line, f, language) for line in lines) if lines else 0
    bw, bh = tw + 2 * pad_x + rule, lh * len(lines) + 2 * pad_y
    y0 = int(64 * s)
    x0 = W - margin - bw if rtl else margin
    frame = max(2, int(3 * s))
    d.rectangle((x0, y0, x0 + bw, y0 + bh), fill=STATUS_BOX, outline=STATUS_RED, width=frame)
    rx = x0 + bw - rule if rtl else x0
    d.rectangle((rx, y0, rx + rule, y0 + bh), fill=STATUS_RED)
    text_x = x0 + pad_x + (0 if rtl else rule)
    _draw_lines(d, lines, f, language, text_x, y0 + pad_y - int(f.size * 0.08), tw, "start",
                STATUS_TEXT, 1.3)
    return _shadowed(layer, 8, 140)


