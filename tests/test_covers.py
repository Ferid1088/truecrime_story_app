"""The covers of a film: the configuration (a theme for every channel, 5 shorts, the platform cover ways marked to_test), the title rules, the avatar's portrait and the
cover itself in every language (long and short) over a scene picture."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.covers import avatar as AV
from app.covers import compose as C
from app.documentary import storage

ROOT = Path(__file__).resolve().parents[1]
OUTFIT = ROOT / "data" / "outfits" / "en" / "navyBlazerBeigeChinos" / "en_navyBlazerBeigeChinos_front.png"
TITLES = {"en": "The open door", "fa": "دری که باز ماند", "ar": "باب لم يُغلق أبدًا", "de": "Die offene Tür", "es": "La puerta abierta", "it": "La porta aperta",
          "fr": "La porte ouverte", "pt": "A porta aberta", "tr": "Hiç kapanmayan kapı"}


@pytest.fixture()
def picture(tmp_path):
    path = tmp_path / "scene.jpg"
    Image.effect_noise((900, 700), 60).convert("RGB").save(path)
    return path


def test_the_configuration_has_a_theme_for_every_channel_and_five_shorts():
    cfg = ai_config.covers_settings()
    assert set(cfg.themes) == set(ai_config.channels)
    assert cfg.shorts_per_episode == 5 <= ai_config.short_clips_settings().max_clips
    assert (cfg.formats["long"].width, cfg.formats["long"].height) == (3840, 2160)
    assert (cfg.formats["short"].width, cfg.formats["short"].height) == (2160, 3840)
    assert (cfg.formats["short"].video_width, cfg.formats["short"].video_height) == (1080, 1920)      # the video and its cover are not the same size
    assert all(t.look and t.prompt for t in cfg.themes.values()) and len({t.signature for t in cfg.themes.values()}) == 9


def test_what_no_upload_has_shown_is_marked_to_test():
    cfg = ai_config.covers_settings()
    assert cfg.safe_zones["status"] == "to_test"
    assert cfg.platforms["instagram_reel"]["cover_status"] == "to_test" and cfg.platforms["facebook_reel"]["cover_status"] == "to_test"
    assert cfg.scene_image["max_images_per_scene"] == 1 and cfg.scene_image["model"] == "gpt-image-2.6-flare" and cfg.scene["source"] == "generated"


def test_a_title_is_a_hook_of_three_to_ten_words_and_turkish_capitals_keep_their_dot():
    assert C.title_issues("The open door") == [] and C.title_issues("one two three four five six seven eight nine ten eleven")
    assert C.title_issues("") and C.title_issues("The refrigerator")
    assert C.display_title("Açık kapı", "tr") == "AÇIK KAPI" and C.display_title("izler", "tr") == "İZLER"
    assert C.display_title("دری که باز ماند", "fa") == "دری که باز ماند"


def test_the_host_is_the_largest_face_of_the_studio_image():
    x, y, w, h = AV.host_face(OUTFIT)
    picture = Image.open(OUTFIT)
    assert 0.3 < (x + w / 2) / picture.width < 0.7 and y / picture.height < 0.3        # the walls' small photographs are not him
    crop = AV.portrait(OUTFIT, 0.5, 2.8)
    assert abs(crop.width / crop.height - 0.5) < 0.02 and crop.height <= picture.height


@pytest.mark.parametrize("language", sorted(TITLES))
def test_every_language_has_its_long_and_its_short_cover(language, picture, tmp_path):
    for kind in ("long", "short"):
        cfg = ai_config.covers_settings()
        a, f = cfg.layout[kind]["avatar"], cfg.formats[kind]
        face = AV.portrait(OUTFIT, a["width"] * f.width / (a["height"] * f.height), a["face_widths"])
        made = C.compose(kind, language, picture, face, storage.ROOT / ai_config.channels[language].logo, TITLES[language], tmp_path / f"{language}_{kind}.jpg", scale=0.15)
        image = Image.open(tmp_path / f"{language}_{kind}.jpg")
        assert image.format == "JPEG" and image.size == (round(f.width * 0.15), round(f.height * 0.15))
        assert made["problems"] == []
        for x, y, w, h in made["elements"].values():
            assert x >= 0 and y >= 0 and x + w <= image.width + 2 and y + h <= image.height + 2


def test_a_right_to_left_cover_mirrors_the_places_of_the_words_and_the_avatar(picture, tmp_path):
    cfg = ai_config.covers_settings()
    a, f = cfg.layout["long"]["avatar"], cfg.formats["long"]
    face = AV.portrait(OUTFIT, a["width"] * f.width / (a["height"] * f.height), a["face_widths"])
    en = C.compose("long", "en", picture, face, storage.ROOT / ai_config.channels["en"].logo, "The open door", tmp_path / "en.jpg", scale=0.15)
    fa = C.compose("long", "fa", picture, face, storage.ROOT / ai_config.channels["fa"].logo, "دری که باز ماند", tmp_path / "fa.jpg", scale=0.15)
    middle = en["size"][0] / 2
    centre = lambda made, name: made["elements"][name][0] + made["elements"][name][2] / 2          # noqa: E731
    assert centre(en, "title") < middle < centre(fa, "title")
    assert centre(en, "avatar") < middle < centre(fa, "avatar") and centre(en, "logo") < middle < centre(fa, "logo")


def test_a_title_that_does_not_fit_or_is_too_long_is_refused(picture, tmp_path):
    face = AV.portrait(OUTFIT, 0.5, 2.8)
    with pytest.raises(C.CoverError, match="words"):
        C.compose("short", "en", picture, face, storage.ROOT / ai_config.channels["en"].logo, "one two three four five six seven eight nine ten eleven", tmp_path / "x.jpg", scale=0.1)


def test_the_short_cover_keeps_its_content_out_of_the_reserved_margins(picture, tmp_path):
    cfg = ai_config.covers_settings()
    face = AV.portrait(OUTFIT, 0.5, 2.8)
    made = C.compose("short", "en", picture, face, storage.ROOT / ai_config.channels["en"].logo, "The open door", tmp_path / "s.jpg", scale=0.1)
    height = made["size"][1]
    for name in ("title", "logo"):
        x, y, w, h = made["elements"][name]
        assert y >= cfg.safe_zones["short"]["top_fraction"] * height and y + h <= (1 - cfg.safe_zones["short"]["bottom_fraction"]) * height
    _x, y, _w, h = made["elements"]["avatar"]
    assert y + h <= (1 - cfg.safe_zones["short"]["avatar_bottom_fraction"]) * height and made["problems"] == []


@pytest.mark.parametrize(("language", "side"), [("en", "right"), ("de", "right"), ("fa", "left"), ("ar", "left")])
def test_the_portrait_of_a_short_cover_sits_in_the_bottom_corner_on_the_side_of_the_language(language, side, picture, tmp_path):
    """Owner decision 2026-11-13: bottom corner, right for left-to-right languages, left for Persian and Arabic; the film cover keeps its place."""
    cfg = ai_config.covers_settings()
    a, f = cfg.layout["short"]["avatar"], cfg.formats["short"]
    face = AV.portrait(OUTFIT, a["width"] * f.width / (a["height"] * f.height), a["face_widths"])
    made = C.compose("short", language, picture, face, storage.ROOT / ai_config.channels[language].logo, TITLES[language], tmp_path / "s.jpg", scale=0.1)
    width, height = made["size"]
    x, y, w, h = made["elements"]["avatar"]
    assert y + h >= 0.95 * height and made["problems"] == []                                # the lowest part of the picture
    assert (x + w >= 0.9 * width) if side == "right" else (x <= 0.1 * width)
    long = C.compose("long", language, picture, AV.portrait(OUTFIT, 0.5, 2.8), storage.ROOT / ai_config.channels[language].logo, TITLES[language], tmp_path / "l.jpg", scale=0.1)
    assert long["elements"]["avatar"][1] < 0.5 * long["size"][1]                          # the film cover is as before: the portrait below the title, not in the corner


@pytest.mark.parametrize("language", ["de", "fr", "en"])
@pytest.mark.parametrize("kind", ["long", "short"])
def test_a_title_reads_on_a_dark_picture_in_every_theme_the_veil_gets_as_strong_as_it_must_be(language, kind, tmp_path):
    """German and French have a light paper theme with dark words: over a dark scene the old thin veil left them unreadable."""
    dark = tmp_path / "dark.jpg"
    Image.new("RGB", (900, 700), (14, 16, 20)).save(dark)
    cfg = ai_config.covers_settings()
    a, f = cfg.layout[kind]["avatar"], cfg.formats[kind]
    face = AV.portrait(OUTFIT, a["width"] * f.width / (a["height"] * f.height), a["face_widths"])
    made = C.compose(kind, language, dark, face, storage.ROOT / ai_config.channels[language].logo, TITLES[language], tmp_path / "c.jpg", scale=0.15)
    assert made["title_contrast"] >= cfg.min_title_contrast and made["problems"] == []
    if language in ("de", "fr"):
        assert made["veil_strength"] > cfg.scrim["start_peak"]


def test_the_logo_is_three_times_and_the_avatar_one_and_a_half_times_the_size_they_had(picture, tmp_path):
    """Before: logo 0.13 (long) / 0.07 (short) of the height, avatar 0.17 x 0.36 (long) / 0.4 x 0.21 (short) of the width and height."""
    cfg = ai_config.covers_settings()
    assert cfg.layout["long"]["logo_height"] == pytest.approx(0.13 * 3) and cfg.layout["short"]["logo_height"] == pytest.approx(0.07 * 3)
    for kind, was in (("long", (0.17, 0.36)), ("short", (0.4, 0.21))):
        avatar = cfg.layout[kind]["avatar"]
        assert avatar["width"] == pytest.approx(was[0] * 1.5, rel=0.02) and avatar["height"] == pytest.approx(was[1] * 1.5, rel=0.02)
        f = cfg.formats[kind]
        face = AV.portrait(OUTFIT, avatar["width"] * f.width / (avatar["height"] * f.height), avatar["face_widths"])
        made = C.compose(kind, "en", picture, face, storage.ROOT / ai_config.channels["en"].logo, "The open door", tmp_path / f"{kind}.jpg", scale=0.15)
        height = made["size"][1]
        assert made["elements"]["logo"][3] >= cfg.layout[kind]["logo_height"] * height * 0.9 and made["problems"] == []


def _cover(kind, language, scene, tmp_path):
    cfg = ai_config.covers_settings()
    a, f = cfg.layout[kind]["avatar"], cfg.formats[kind]
    face = AV.portrait(OUTFIT, a["width"] * f.width / (a["height"] * f.height), a["face_widths"])
    return C.compose(kind, language, scene, face, storage.ROOT / ai_config.channels[language].logo, TITLES[language], tmp_path / f"{language}_{kind}.jpg", scale=0.15)


def _plain(tmp_path, name, colour):
    path = tmp_path / f"{name}.jpg"
    Image.new("RGB", (900, 700), colour).save(path)
    return path


@pytest.mark.parametrize("language", sorted(TITLES))
@pytest.mark.parametrize("kind", ["long", "short"])
@pytest.mark.parametrize("colour", [(14, 16, 20), (238, 236, 228), (128, 128, 128)], ids=["dark", "light", "grey"])
def test_the_channel_mark_is_the_same_word_in_every_language_and_reads_on_every_picture(language, kind, colour, tmp_path):
    made = _cover(kind, language, _plain(tmp_path, "scene", colour), tmp_path)
    mark = made["watermark"]
    assert mark["text"] == "TrueCrime"                                                    # never translated
    assert mark["contrast"] >= ai_config.covers_settings().watermark["min_contrast"]
    assert [p for p in made["problems"] if "channel mark" in p] == []


def test_the_colour_of_the_channel_mark_follows_the_picture(tmp_path):
    dark = _cover("long", "en", _plain(tmp_path, "dark", (14, 16, 20)), tmp_path)["watermark"]
    light = _cover("long", "en", _plain(tmp_path, "light", (238, 236, 228)), tmp_path)["watermark"]
    assert dark["color"] != light["color"] and dark["veil_strength"] == 0 == light["veil_strength"]


@pytest.mark.parametrize("language", ["en", "fa"])
@pytest.mark.parametrize("kind", ["long", "short"])
def test_the_channel_mark_keeps_clear_of_the_title_the_portrait_and_the_logo(language, kind, picture, tmp_path):
    made = _cover(kind, language, picture, tmp_path)
    x, y, w, h = made["elements"]["watermark"]
    for name in ("title", "avatar", "logo"):
        ox, oy, ow, oh = made["elements"][name]
        assert x + w <= ox or ox + ow <= x or y + h <= oy or oy + oh <= y, name


def test_the_channel_mark_takes_the_calmest_field(tmp_path):
    cfg = ai_config.covers_settings()
    fields = cfg.watermark["fields"]["long"]
    scene = Image.effect_noise((960, 540), 70).convert("RGB")
    calm = fields[3]
    scene.paste((20, 24, 30), (round(calm["x"] * 960), round(calm["y"] * 540), round((calm["x"] + calm["width"]) * 960) + 1, round((calm["y"] + calm["height"]) * 540) + 1))
    path = tmp_path / "scene.jpg"
    scene.save(path)
    made = _cover("long", "en", path, tmp_path)
    width, height = made["size"]
    x, y, w, h = made["elements"]["watermark"]
    assert abs((x + w / 2) / width - (calm["x"] + calm["width"] / 2)) < calm["width"] / 2 and abs((y + h / 2) / height - (calm["y"] + calm["height"] / 2)) < calm["height"] / 2


def test_a_title_has_one_colour_on_the_film_cover(tmp_path):
    made = _cover("long", "de", _plain(tmp_path, "scene", (128, 128, 128)), tmp_path)
    assert len(made["title_lines"]) >= 1 and "min_accent_contrast" not in ai_config.covers_settings().model_dump()


HOOKS = {"fa": "ترسناک‌ترین غریبه همان است که کلید دارد", "en": "The killer who was never meant to be found", "ar": "القاتل الذي لم يكن يُفترض أن يُكتشف أبدًا",
         "de": "Der Mörder, der nie gefunden werden sollte"}


@pytest.mark.parametrize("kind", ["long", "short"])
@pytest.mark.parametrize("language", sorted(HOOKS))
def test_a_hook_is_set_in_lines_of_about_the_same_width_as_large_as_the_layout_allows(language, kind):
    cfg = ai_config.covers_settings()
    font, lines, size = C.fit_title(C.display_title(HOOKS[language], language), language, kind)
    layout, height = cfg.layout[kind]["title"], cfg.formats[kind].height
    assert layout["min_size"] * height <= size <= layout["size"] * height
    assert len(lines) <= layout["max_lines"] and len(lines) * size * C.LINE_HEIGHT <= layout["max_height"] * height + 1
    widths = [C.TY.text_width(line, font, language) for line in lines]
    assert max(widths) <= layout["width"] * cfg.formats[kind].width and (len(lines) == 1 or min(widths) >= 0.4 * max(widths))      # no word left alone


def test_a_title_too_long_to_read_is_sent_back_to_the_planner_with_the_fix(monkeypatch):
    from app.covers import proposals as P

    monkeypatch.setattr(P, "reveal_issues", lambda *args: [])                        # the story's spoilers are not what is tested here

    long_words = " ".join(["Unbelievably"] * 8)
    with pytest.raises(C.CoverError, match="too long to read"):
        C.fit_title(long_words.upper(), "en", "short")
    item = {"key": "C1", "title": long_words, "scene": "", "confidence": None, "reason": ""}
    issues = P.item_issues(item, None, 0, "en")
    assert [i["type"] for i in issues if i["type"].startswith("title")] == ["title_unreadable"]
