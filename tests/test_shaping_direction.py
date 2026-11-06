"""Right-to-left lines drawn without raqm: the order is visual and the paired marks are mirrored, so a Persian quotation
reads «…» as the language writes it (the opening mark at the right of the quoted words)."""

from __future__ import annotations

from app.documentary.visuals import shaping


def visual(text: str) -> str:
    return shaping.get_display(text)


def test_persian_guillemets_are_mirrored_in_the_visual_string():
    out = visual("گفت: «سلام»")
    assert out[0] == "«" or out.index("«") < out.index("»"), out
    assert out.index("«") < out.index("»")        # left to right on the screen: « then the words then »


def test_brackets_around_persian_are_mirrored():
    out = visual("(سلام)")
    assert out[0] == "(" and out[-1] == ")"


def test_latin_text_is_left_alone():
    assert shaping.display("«abc»", "en") == "«abc»"
