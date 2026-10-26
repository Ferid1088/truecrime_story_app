"""Text measuring and wrapping that is right for Persian and Arabic whether or not Pillow has the raqm layout
engine (it needs libraqm and fribidi, which the macOS wheels do not find by themselves): without raqm the
text is reshaped (joined letter forms) and put in visual order by pure-Python libraries, then drawn left to right."""

from __future__ import annotations

import arabic_reshaper
from bidi import get_display
from PIL import ImageFont, features

RTL = {"fa", "ar"}
RAQM = features.check("raqm")
_reshaper = arabic_reshaper.ArabicReshaper(configuration={"delete_harakat": False, "support_ligatures": True})


def display(text: str, language: str) -> str:
    """What the font is asked to draw for a logical line."""
    if language in RTL and not RAQM:
        return get_display(_reshaper.reshape(text))
    return text


def kwargs(language: str) -> dict:
    """Pillow draw/measure options: raqm needs the direction; the fallback draws the prepared visual string."""
    if not RAQM:
        return {}
    return {"direction": "rtl" if language in RTL else "ltr", "language": language}


def width(text: str, f: ImageFont.FreeTypeFont, language: str) -> int:
    left, _, right, _ = f.getbbox(display(text, language), **kwargs(language))
    return right - left


def wrap(text: str, f: ImageFont.FreeTypeFont, language: str, max_width: int) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    for word in text.split():
        trial = " ".join([*current, word])
        if current and width(trial, f, language) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines
