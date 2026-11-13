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
TITLES = {"en": "The open door", "fa": "درِ باز", "ar": "الباب المفتوح", "de": "Die offene Tür", "es": "La puerta abierta", "it": "La porta aperta",
          "fr": "La porte ouverte", "pt": "A porta aberta", "tr": "Açık kapı"}


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


def test_a_title_has_one_to_four_words_and_turkish_capitals_keep_their_dot():
    assert C.title_issues("The open door") == [] and C.title_issues("one two three four five")
    assert C.title_issues("")
    assert C.display_title("Açık kapı", "tr") == "AÇIK KAPI" and C.display_title("izler", "tr") == "İZLER"
    assert C.display_title("درِ باز", "fa") == "درِ باز"


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
    fa = C.compose("long", "fa", picture, face, storage.ROOT / ai_config.channels["fa"].logo, "درِ باز", tmp_path / "fa.jpg", scale=0.15)
    middle = en["size"][0] / 2
    centre = lambda made, name: made["elements"][name][0] + made["elements"][name][2] / 2          # noqa: E731
    assert centre(en, "title") < middle < centre(fa, "title")
    assert centre(en, "avatar") < middle < centre(fa, "avatar") and centre(en, "logo") < middle < centre(fa, "logo")


def test_a_title_that_does_not_fit_or_is_too_long_is_refused(picture, tmp_path):
    face = AV.portrait(OUTFIT, 0.5, 2.8)
    with pytest.raises(C.CoverError, match="words"):
        C.compose("short", "en", picture, face, storage.ROOT / ai_config.channels["en"].logo, "one two three four five six", tmp_path / "x.jpg", scale=0.1)


def test_the_short_cover_keeps_its_content_out_of_the_reserved_margins(picture, tmp_path):
    cfg = ai_config.covers_settings()
    face = AV.portrait(OUTFIT, 0.5, 2.8)
    made = C.compose("short", "en", picture, face, storage.ROOT / ai_config.channels["en"].logo, "The open door", tmp_path / "s.jpg", scale=0.1)
    height = made["size"][1]
    for name in ("title", "logo", "avatar"):
        x, y, w, h = made["elements"][name]
        assert y >= cfg.safe_zones["short"]["top_fraction"] * height and y + h <= (1 - cfg.safe_zones["short"]["bottom_fraction"]) * height


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
