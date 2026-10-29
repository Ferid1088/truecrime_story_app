"""The frame of a card: a decoration drawn on the page ground, in the kind's own accent, that tells the viewer which
kind of card this is before a word is read. Frames stay out of the bottom strip the subtitles use, are mirrored for
right-to-left languages and are part of the ground, so they never count as content."""

from __future__ import annotations

from PIL import Image, ImageDraw

from app.documentary.graphics import theme as T

Color = tuple[int, int, int]
SUBTITLE_STRIP = 200          # the bottom strip of the page that the subtitles use (config safe_zone.bottom)
INSET = 32


def _box(size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The area frames may use: the page without a margin of INSET and without the subtitle strip."""
    w, h = size
    return INSET, INSET, w - INSET, h - T.config().safe_zone["bottom"] - 24


def draw(img: Image.Image, frame: str, accent: Color, rule: Color, rtl: bool) -> Image.Image:
    """`img` (RGB, the page ground) with the frame of this style drawn on it."""
    w, h = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def x(left: float, width: float = 0) -> float:
        """Where a box measured from the reading start (left for Latin, right for Persian and Arabic) begins."""
        return w - left - width if rtl else left

    def end(offset: float, width: float = 0) -> float:
        """Where a box measured from the opposite edge begins: frames that sit at the end of the line, away from the
        header."""
        return offset if rtl else w - offset - width

    left, top, right, bottom = _box(img.size)
    if frame == "rail":
        d.rectangle((x(0, 18), 0, x(0, 18) + 18, h), fill=(*accent, 255))
        d.rectangle((x(18, 70), 0, x(18, 70) + 70, 12), fill=(*accent, 255))
    elif frame == "top_band":
        d.rectangle((0, 0, w, 14), fill=(*accent, 255))
        d.rectangle((x(120, 260), 14, x(120, 260) + 260, 24), fill=(*accent, 150))
    elif frame == "corners":
        arm, t = 64, 6
        for cx, cy, sx, sy in ((left, top, 1, 1), (right, top, -1, 1), (left, bottom, 1, -1), (right, bottom, -1, -1)):
            d.rectangle((min(cx, cx + sx * arm), min(cy, cy + sy * t), max(cx, cx + sx * arm), max(cy, cy + sy * t)),
                        fill=(*accent, 255))
            d.rectangle((min(cx, cx + sx * t), min(cy, cy + sy * arm), max(cx, cx + sx * t), max(cy, cy + sy * arm)),
                        fill=(*accent, 255))
    elif frame == "inset":
        d.rectangle((left, top, right, bottom), outline=(*accent, 110), width=3)
        for cx, cy in ((left, top), (right, top), (left, bottom), (right, bottom)):
            d.rectangle((cx - 9, cy - 9, cx + 9, cy + 9), fill=(*accent, 255))
    elif frame == "tabs":
        d.rectangle((end(120, 320), 0, end(120, 320) + 320, 20), fill=(*accent, 255))
        d.rectangle((end(120 + 320, 70), 0, end(120 + 320, 70) + 70, 36), fill=(*accent, 255))
        d.rectangle((end(120, 320), 20, end(120, 320) + 320, 26), fill=(*rule, 255))
    elif frame == "dots":
        for row in range(4):
            for col in range(14):
                cx, cy = end(120 + col * 28), 30 + row * 28
                d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(*accent, 140 - row * 25))
    elif frame == "stripes":
        stripes = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(stripes)
        for k in range(-w, w, 20):
            sd.line((k, 0, k - 160, 160) if not rtl else (k, 0, k + 160, 160), fill=(*accent, 210), width=6)
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).polygon([(end(0), 0), (end(260), 0), (end(0), 260)], fill=255)
        layer.paste(stripes, (0, 0), mask)
    elif frame == "double_rule":
        for dy, color in ((34, accent), (44, rule)):
            d.rectangle((x(120, w - 240), dy, x(120, w - 240) + (w - 240), dy + 3), fill=(*color, 255))
    out = img.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")
