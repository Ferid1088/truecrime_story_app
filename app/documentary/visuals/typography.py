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
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
RTL = {"fa", "ar"}
RAQM = features.check("raqm")

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


def _kw(language: str) -> dict:
    if not RAQM:
        return {}
    if language in RTL:
        return {"direction": "rtl", "language": language}
    return {"direction": "ltr", "language": language}


def text_width(text: str, f: ImageFont.FreeTypeFont, language: str) -> int:
    l, _, r, _ = f.getbbox(text, **_kw(language))
    return r - l


def wrap(text: str, f: ImageFont.FreeTypeFont, language: str, max_width: int) -> list[str]:
    words, lines, cur = text.split(), [], []
    for w in words:
        trial = " ".join(cur + [w])
        if cur and text_width(trial, f, language) > max_width:
            lines.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    return lines


def _draw_lines(draw, lines, f, language, box_x, y, width, align, fill, spacing):
    kw = _kw(language)
    for line in lines:
        w = text_width(line, f, language)
        if align == "center":
            x = box_x + (width - w) / 2
        elif (align == "start" and language in RTL) or align == "end":
            x = box_x + width - w
        else:
            x = box_x
        draw.text((x, y), line, font=f, fill=fill, **kw)
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
    if kind == "quote":
        f = font(language, "serif", "regular", int(56 * s))
        width = int(W * 0.68)
        lines = wrap(text, f, language, width)
        total = int(len(lines) * f.size * 1.35)
        y = (H - total) // 2
        q_open, q_close = ("«", "»") if language in ("fa", "ar") else (
            ("„", "“") if language == "de" else ("“", "”"))
        lines = [f"{q_open}{lines[0]}"] + lines[1:]
        lines[-1] = lines[-1] + q_close
        _draw_lines(d, lines, f, language, (W - width) // 2, y, width, "center",
                    (245, 242, 235, 255), 1.35)
        return _shadowed(layer, 10, 200)
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
        d.text((x, int(36 * s)), text, font=f, fill=(235, 235, 235, 190), **_kw(language))
        return _shadowed(layer, 3, 150)
    if kind == "credit":
        f = font("en", "sans", "regular", int(20 * s))
        w = text_width(text, f, "en")
        d.text((W - margin // 2 - w, H - int(40 * s)), text, font=f,
               fill=(235, 235, 235, 170))
        return _shadowed(layer, 3, 140)
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


def document_card(title: str, publisher: str | None, passage: str, context: str,
                  W: int = 2400, H: int = 1350) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """A page of the real source text (the passage in its context) on
    paper over a dark desk, the passage marked with a highlighter.
    Returns (image, highlight bbox) for DOCUMENT_HIGHLIGHT. The text is
    the source's own wording — never paraphrased."""
    img = Image.new("RGB", (W, H), (24, 25, 27))
    pw, ph = int(W * 0.62), int(H * 1.08)
    px, py = (W - pw) // 2, int(H * 0.04)
    page = Image.new("RGB", (pw, ph), (236, 231, 218))
    noise = Image.effect_noise((pw, ph), 14).convert("L")
    page = Image.composite(page, Image.new("RGB", (pw, ph), (224, 218, 202)),
                           noise.point(lambda v: 255 if v > 110 else 225))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle((px + 14, py + 18, px + pw + 14, py + ph + 18),
                                     fill=(0, 0, 0, 160))
    img.paste(shadow.filter(ImageFilter.GaussianBlur(18)).convert("RGB"), (0, 0),
              shadow.filter(ImageFilter.GaussianBlur(18)))
    img.paste(page, (px, py))
    d = ImageDraw.Draw(img, "RGBA")
    m = int(pw * 0.09)
    x0 = px + m
    width = pw - 2 * m
    head = font("en", "sans", "bold", int(H * 0.026))
    sub = font("en", "sans", "regular", int(H * 0.022))
    body = font("en", "typewriter", "regular", int(H * 0.031))
    y = py + int(H * 0.07)
    d.text((x0, y), (publisher or "").upper()[:70], font=head, fill=(60, 55, 50, 255))
    y += int(head.size * 1.5)
    for line in wrap(title[:160], sub, "en", width)[:2]:
        d.text((x0, y), line, font=sub, fill=(95, 88, 80, 255))
        y += int(sub.size * 1.35)
    y += int(H * 0.012)
    d.line((x0, y, x0 + width, y), fill=(90, 85, 78, 170), width=3)
    y += int(H * 0.035)
    flat = " ".join(context.split())
    pas = " ".join(passage.split())
    i = flat.find(pas)
    if i < 0:
        flat, i = pas, 0
    lo, hi = max(0, i - 520), min(len(flat), i + len(pas) + 520)
    before, after = flat[lo:i], flat[i + len(pas):hi]
    if lo > 0:
        before = "… " + before.split(" ", 1)[-1]
    if hi < len(flat):
        after = after.rsplit(" ", 1)[0] + " …"
    raw_tokens = [(w, False) for w in before.split()] + [(w, True) for w in pas.split()] + \
        [(w, False) for w in after.split()]
    tokens: list[tuple[str, bool]] = []
    for w, marked in raw_tokens:
        # punctuation that followed the passage sticks to its last word
        if tokens and not marked and w[:1] in ".,;:!?)" and tokens[-1][1]:
            head_p = len(w) - len(w.lstrip(".,;:!?)"))
            tokens[-1] = (tokens[-1][0] + w[:head_p], True)
            w = w[head_p:]
            if not w:
                continue
        tokens.append((w, marked))
    space = text_width(" ", body, "en") or int(body.size * 0.5)
    lines, cur, cur_w = [], [], 0
    for w, marked in tokens:
        ww = text_width(w, body, "en")
        if cur and cur_w + space + ww > width:
            lines.append(cur)
            cur, cur_w = [], 0
        cur.append((w, marked, ww))
        cur_w += (space if len(cur) > 1 else 0) + ww
    if cur:
        lines.append(cur)
    lh = int(body.size * 1.55)
    # keep the passage on the visible part of the page
    first = next((n for n, ln in enumerate(lines) if any(mk for _, mk, _ in ln)), 0)
    max_lines = int((py + min(ph, H) - y - int(H * 0.05)) / lh)
    start = max(0, min(first - 3, len(lines) - max_lines))
    bbox = [W, H, 0, 0]
    for ln in lines[start:start + max_lines]:
        x = x0
        for w, marked, ww in ln:
            if marked:
                d.rectangle((x - 6, y + int(body.size * 0.08), x + ww + 6, y + int(body.size * 1.18)),
                            fill=(244, 214, 92, 150))
                bbox = [min(bbox[0], x - 6), min(bbox[1], y), max(bbox[2], x + ww + 6),
                        max(bbox[3], y + int(body.size * 1.2))]
            d.text((x, y), w, font=body, fill=(32, 30, 28, 255))
            x += ww + space
        y += lh
    if bbox[2] <= bbox[0]:
        bbox = [x0, int(H * 0.4), x0 + width, int(H * 0.5)]
    return img, tuple(int(v) for v in bbox)
