"""Frame 0 of a host-led reel: Fereidoun's portrait filling the whole 1080x1920 frame, with the
channel's "true crime" label (owner-approved wording, config/parameters/shorts_host_led.json's
cover_label) burned directly onto the image.

The portrait is scaled to COVER the full frame (never a small inset floating on a black
background) and centre-cropped. The label sits on a solid scrim band inside the top safe area,
centred, so it stays legible over any photo and inside the margins platforms reserve for their
own UI (handle/caption at the bottom, buttons on the right).

Persian is longer than the other three labels, so it is set on two lines, each line's width
measured with the real font (typography.text_width) and never shrunk below
LEGIBILITY_MIN_PX. RTL languages (fa, ar) still shape correctly (raqm) even though the label is
centred rather than aligned to a side.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from PIL import Image, ImageDraw

from app.core.ai_config import ai_config
from app.documentary.visuals import typography as T

W, H = 1080, 1920
LEGIBILITY_MIN_PX = 40
LABEL_MAX_PX = 96
# Platform-chrome safe area: nothing readable may sit in these margins (handle/caption/engagement
# buttons on Shorts/Reels/TikTok all reserve this much).
SIDE_SAFE_MARGIN = 90
TOP_SAFE_MARGIN = 150
BOTTOM_SAFE_MARGIN = 260
LABEL_BAND_COLOR = (10, 10, 12)
LABEL_TEXT_COLOR = (245, 242, 235)
LABEL_BAND_PADDING = 34


@dataclass(frozen=True)
class CoverLayout:
    image: Image.Image
    label_lines: list[str]
    label_font_px: int
    label_box: tuple[int, int, int, int]
    contrast_ratio: float


def _force_lines(words: list[str], f, language: str, n: int) -> list[str]:
    """Split ``words`` into exactly ``n`` lines at word boundaries, choosing the breaks that
    keep the widest line as narrow as possible. A label designed to occupy n lines must not
    collapse to fewer just because a different shaping engine measures it narrower."""
    if len(words) <= n:
        return list(words)
    best: list[str] | None = None
    best_width = 0
    for cuts in itertools.combinations(range(1, len(words)), n - 1):
        groups = [" ".join(words[a:b])
                  for a, b in zip((0, *cuts), (*cuts, len(words)), strict=True)]
        widest = max(T.text_width(g, f, language) for g in groups)
        if best is None or widest < best_width:
            best, best_width = groups, widest
    return best or list(words)


def _fit_label(text: str, language: str, max_width: int, max_lines: int,
               min_lines: int = 1) -> tuple[list[str], int]:
    """The largest font size (down to LEGIBILITY_MIN_PX) whose wrap fits in max_lines lines
    of max_width; never returns more lines than max_lines or a smaller font than the minimum.
    ``min_lines`` enforces the label's designed line count (the Persian label is set on two
    lines): a wrap that comes out shorter is split at a word boundary, at the largest size
    where every line still fits."""
    words = text.split()
    for size in range(LABEL_MAX_PX, LEGIBILITY_MIN_PX - 1, -2):
        f = T.font(language, "sans", "bold", size)
        lines = T.wrap(text, f, language, max_width)
        if len(lines) < min_lines:
            forced = _force_lines(words, f, language, min_lines)
            if not all(T.text_width(line, f, language) <= max_width for line in forced):
                continue                                # still too wide: try a smaller size
            lines = forced
        if len(lines) <= max_lines:
            return lines, size
    f = T.font(language, "sans", "bold", LEGIBILITY_MIN_PX)
    lines = T.wrap(text, f, language, max_width)
    if len(lines) < min_lines:
        lines = _force_lines(words, f, language, min_lines)
    return lines[:max_lines], LEGIBILITY_MIN_PX


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    """WCAG contrast ratio between two colours (>= 4.5 is legible body-text contrast)."""
    l1, l2 = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _cover_crop(portrait: Image.Image) -> Image.Image:
    """Scale ``portrait`` to fully COVER the 1080x1920 frame (never letterboxed, never a small
    inset on a black background), then centre-crop the excess. Biases the crop slightly toward
    the upper half of the source, since a seated host's head tends to sit above the vertical
    centre of a landscape studio photo."""
    scale = max(W / portrait.width, H / portrait.height)
    new_w, new_h = round(portrait.width * scale), round(portrait.height * scale)
    resized = portrait.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - W) // 2)
    top = max(0, min((new_h - H) // 3, new_h - H))
    return resized.crop((left, top, left + W, top + H))


def compose_cover(language: str, portrait: Image.Image, *, background: tuple[int, int, int] = (12, 12, 14)) -> CoverLayout:
    """Frame 0: a full-bleed portrait crop with the label burned onto a scrim band inside the
    top safe area, centred, legible over any underlying photo."""
    label_text = ai_config.shorts_host_led.cover_label.get(language)
    if not label_text:
        raise ValueError(f"no approved cover label configured for language {language!r}")
    img = _cover_crop(portrait).convert("RGB")

    label_width = W - 2 * SIDE_SAFE_MARGIN
    label_lines = 2 if language == "fa" else 1
    lines, size = _fit_label(label_text, language, label_width, label_lines, label_lines)

    draw = ImageDraw.Draw(img)
    f = T.font(language, "sans", "bold", size)
    line_height = int(size * 1.3)
    total_h = line_height * len(lines)
    band_y0 = TOP_SAFE_MARGIN
    band_y1 = band_y0 + total_h + 2 * LABEL_BAND_PADDING
    draw.rectangle((0, band_y0, W, band_y1), fill=LABEL_BAND_COLOR)
    text_y0 = band_y0 + LABEL_BAND_PADDING
    T._draw_lines(draw, lines, f, language, SIDE_SAFE_MARGIN, text_y0, label_width,
                  "center", (*LABEL_TEXT_COLOR, 255), 1.3)
    box = (SIDE_SAFE_MARGIN, text_y0, W - SIDE_SAFE_MARGIN, text_y0 + total_h)
    return CoverLayout(image=img, label_lines=lines, label_font_px=size,
                       label_box=box, contrast_ratio=contrast_ratio(LABEL_TEXT_COLOR, LABEL_BAND_COLOR))


def thumbnail_legible(layout: CoverLayout, *, downscale_to: tuple[int, int] = (120, 213)) -> bool:
    """A coarse legibility proxy at thumbnail size: the label's line height, once the image is
    downscaled to ``downscale_to``, must still be at least a few pixels tall and contrast must
    hold (WCAG downscaling does not change relative contrast, only apparent sharpness)."""
    scale = downscale_to[1] / H
    scaled_line_px = layout.label_font_px * scale
    return scaled_line_px >= 3.0 and layout.contrast_ratio >= 4.5


def within_safe_area(layout: CoverLayout) -> bool:
    """The label box must sit entirely inside the side/top/bottom platform-chrome safe area."""
    x0, y0, x1, y1 = layout.label_box
    return (x0 >= SIDE_SAFE_MARGIN and x1 <= W - SIDE_SAFE_MARGIN
            and y0 >= TOP_SAFE_MARGIN and y1 <= H - BOTTOM_SAFE_MARGIN)
