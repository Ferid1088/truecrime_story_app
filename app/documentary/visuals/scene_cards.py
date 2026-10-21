"""Typographic scenes for the picture track: a spoken sentence set large, the board "what we
know so far" and two accounts side by side. They are drawn from the film's own words and the case's
own facts on a dark ground (nothing is invented); the headings come localized by the caller."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from app.documentary.visuals.typography import RTL, _draw_lines, font, text_width, wrap

W, H = 1920, 1080
GROUND = (14, 14, 16)
WARM = (245, 242, 235)
GREY = (170, 168, 160)
RED = (206, 44, 40)


def _ground() -> Image.Image:
    img = Image.new("RGB", (W, H), GROUND)
    vignette = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vignette).ellipse((-W * 0.2, -H * 0.3, W * 1.2, H * 1.3), fill=70)
    vignette = vignette.filter(ImageFilter.GaussianBlur(160))
    return Image.composite(Image.new("RGB", (W, H), (34, 33, 36)), img, vignette)


def _block_height(lines: list[str], size: float, spacing: float) -> int:
    return int(len(lines) * size * spacing)


def text_scene(text: str, language: str) -> Image.Image:
    """One spoken sentence, large and centred, under a short red rule."""
    img, d = _ground(), None
    d = ImageDraw.Draw(img)
    f = font(language, "serif", "regular", 66)
    width = int(W * 0.72)
    lines = wrap(" ".join(text.split()), f, language, width)[:6]
    top = (H - _block_height(lines, f.size, 1.38)) // 2
    d.rectangle(((W - 90) // 2, top - 60, (W + 90) // 2, top - 54), fill=RED)
    _draw_lines(d, lines, f, language, (W - width) // 2, top, width, "center", WARM, 1.38)
    return img


def split(heading: str, left: tuple[str, str], right: tuple[str, str], language: str) -> Image.Image:
    """Two accounts of the same thing side by side: (who, what they say) each."""
    img = _ground()
    d = ImageDraw.Draw(img)
    fh = font(language, "sans", "bold", 36)
    hw = text_width(heading, fh, language)
    _draw_lines(d, [heading], fh, language, (W - hw) // 2, 90, hw, "center", GREY, 1.2)
    sides = [right, left] if language in RTL else [left, right]
    col = (W - 3 * 120) // 2
    f = font(language, "serif", "regular", 46)
    fw = font(language, "sans", "bold", 38)
    texts = [wrap(" ".join(says.split()), f, language, col)[:9] for _, says in sides]
    block = 110 + max(_block_height(lines, f.size, 1.35) for lines in texts)
    top = max(200, (H - block) // 2)
    for i, ((who, _), lines) in enumerate(zip(sides, texts, strict=True)):
        x = 120 + i * (col + 120)
        d.rectangle((x, top, x + 60, top + 5), fill=RED)
        _draw_lines(d, [who], fw, language, x, top + 25, col, "start", RED, 1.2)
        _draw_lines(d, lines, f, language, x, top + 110, col, "start", WARM, 1.35)
    d.line((W // 2, top, W // 2, top + block), fill=(70, 70, 74), width=2)
    return img
