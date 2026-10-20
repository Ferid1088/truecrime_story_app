from PIL import Image

from app.shortform.host_led_cover import (
    LEGIBILITY_MIN_PX,
    H,
    W,
    compose_cover,
    thumbnail_legible,
    within_safe_area,
)


def _portrait() -> Image.Image:
    return Image.new("RGB", (800, 1200), (90, 70, 60))


def test_cover_is_full_reel_frame():
    layout = compose_cover("en", _portrait())
    assert layout.image.size == (W, H)


def test_portrait_fills_the_frame_no_small_inset():
    # A full-bleed cover crop has no uniform background colour left anywhere: every pixel came
    # from the (resized) portrait. The old broken design pasted a small inset onto a solid
    # background colour, leaving large uniform regions of exactly that colour.
    layout = compose_cover("en", _portrait())
    px = layout.image.load()
    background = (12, 12, 14)
    uniform = sum(1 for x in range(0, W, 20) for y in range(0, H, 20) if px[x, y] == background)
    total = len(range(0, W, 20)) * len(range(0, H, 20))
    assert uniform / total < 0.05, "cover still mostly background colour, not a full-bleed photo"


def test_label_wording_comes_from_config_verbatim():
    layout = compose_cover("en", _portrait())
    assert "".join(layout.label_lines).replace(" ", "") == "TRUECRIME"


def test_persian_label_wraps_to_two_lines_never_below_minimum():
    layout = compose_cover("fa", _portrait())
    assert len(layout.label_lines) == 2
    assert layout.label_font_px >= LEGIBILITY_MIN_PX


def test_arabic_label_is_single_line():
    layout = compose_cover("ar", _portrait())
    assert len(layout.label_lines) == 1


def test_label_sits_inside_platform_safe_area():
    for lang in ("en", "de", "fa", "ar"):
        layout = compose_cover(lang, _portrait())
        assert within_safe_area(layout), f"{lang} label box leaves the platform safe area"


def test_legible_at_thumbnail_size():
    for lang in ("en", "de", "fa", "ar"):
        layout = compose_cover(lang, _portrait())
        assert thumbnail_legible(layout), f"{lang} cover not legible at thumbnail size"


def test_missing_label_config_is_a_hard_error():
    import pytest

    with pytest.raises(ValueError):
        compose_cover("xx", _portrait())
