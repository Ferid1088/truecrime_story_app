"""The covers of a film: the configuration (a theme for every channel, 5 shorts, the platform cover ways marked to_test), the title rules, the avatar's portrait, the
scene's gates and the cover itself in every language (long and short)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.covers import avatar as AV
from app.covers import compose as C
from app.covers import scene as SC
from app.covers import service as SV
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


def _asset(**over):
    base = dict(verification_status="verified", asset_type="photo", provider="web", found_during="research", rights_status="public_domain", width=1600, height=1000,
                description="a house at night", caption="", subject_type="building", entity_type="place", verification_json="{}", editorial_approved_at=None,
                asset_code="VIS_1", local_path="x", relevance_tier=3)
    return SimpleNamespace(**{**base, **over})


def test_the_configuration_has_a_theme_for_every_channel_and_five_shorts():
    cfg = ai_config.covers_settings()
    assert set(cfg.themes) == set(ai_config.channels)
    assert cfg.shorts_per_episode == 5 <= ai_config.short_clips_settings().max_clips
    assert (cfg.formats["long"].width, cfg.formats["long"].height) == (3840, 2160)
    assert (cfg.formats["short"].width, cfg.formats["short"].height) == (2160, 3840)
    assert (cfg.formats["short"].video_width, cfg.formats["short"].video_height) == (1080, 1920)      # the video and its cover are not the same size
    assert len({t.signature for t in cfg.themes.values()}) == 9


def test_what_no_upload_has_shown_is_marked_to_test():
    cfg = ai_config.covers_settings()
    assert cfg.safe_zones["status"] == "to_test"
    assert cfg.platforms["instagram_reel"]["cover_status"] == "to_test" and cfg.platforms["facebook_reel"]["cover_status"] == "to_test"
    assert cfg.fallback_image["enabled"] is False and cfg.fallback_image["max_images_per_scene"] == 1


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


@pytest.mark.parametrize("change,reason", [({"verification_status": "pending"}, "not verified"), ({"description": "two people in a yard"}, "person"),
                                           ({"entity_type": "person"}, "person"), ({"provider": "generated"}, "real"), ({"width": 300}, "resolution"),
                                           ({"description": "a blood stain"}, "graphic")])
def test_a_scene_is_refused_for_these_reasons(change, reason):
    assert reason in (SC.reject_reason(_asset(**change), "object", set()) or "")


def test_a_victim_or_suspect_is_never_a_scene_and_a_house_can_be():
    assert "victim" in SC.reject_reason(_asset(), "victim", set())
    assert SC.reject_reason(_asset(), "place", set()) is None


def test_scenes_are_reused_only_when_there_are_too_few():
    options = [{"code": "A"}, {"code": "B"}]
    picked = SC.pick(options, 3)
    assert [p["code"] for p in picked] == ["A", "B", "A"] and [p["reused"] for p in picked] == [False, False, True]
    with pytest.raises(SC.SceneMissing, match="fallback image model is off"):
        SC.pick([], 1)


@pytest.mark.parametrize("language", sorted(TITLES))
def test_every_language_has_its_long_and_its_short_cover(language, picture, tmp_path):
    for kind in ("long", "short"):
        cfg = ai_config.covers_settings()
        a, f = cfg.layout[kind]["avatar"], cfg.formats[kind]
        face = AV.portrait(OUTFIT, a["width"] * f.width / (a["height"] * f.height), a["face_widths"])
        made = C.compose(kind, language, picture, face, storage.ROOT / ai_config.channels[language].logo, TITLES[language], tmp_path / f"{language}_{kind}.jpg", scale=0.15)
        image = Image.open(tmp_path / f"{language}_{kind}.jpg")
        assert image.format == "JPEG" and image.size == (round(f.width * 0.15), round(f.height * 0.15)) and made["signature"] == cfg.themes[language].signature
        assert made["problems"] == []
        for x, y, w, h in made["elements"].values():
            assert x >= 0 and y >= 0 and x + w <= image.width + 2 and y + h <= image.height + 2


def test_a_right_to_left_cover_mirrors_the_places_not_the_pictures(picture, tmp_path):
    cfg = ai_config.covers_settings()
    a, f = cfg.layout["long"]["avatar"], cfg.formats["long"]
    face = AV.portrait(OUTFIT, a["width"] * f.width / (a["height"] * f.height), a["face_widths"])
    en = C.compose("long", "en", picture, face, storage.ROOT / ai_config.channels["en"].logo, "The open door", tmp_path / "en.jpg", scale=0.15)
    fa = C.compose("long", "fa", picture, face, storage.ROOT / ai_config.channels["fa"].logo, "درِ باز", tmp_path / "fa.jpg", scale=0.15)
    middle = en["size"][0] / 2
    centre = lambda made, name: made["elements"][name][0] + made["elements"][name][2] / 2          # noqa: E731
    assert centre(en, "title") < middle < centre(fa, "title") and centre(en, "scene") > middle > centre(fa, "scene")
    assert centre(en, "avatar") < middle and centre(fa, "avatar") > middle


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


def test_the_service_makes_one_long_and_a_cover_for_each_short_and_a_manifest(monkeypatch, picture, tmp_path):
    monkeypatch.setattr(storage, "case_dir", lambda case_id: tmp_path / str(case_id))
    monkeypatch.setattr(SV, "film_avatar", lambda db, case_id, language: ("en/navyBlazerBeigeChinos", OUTFIT))
    monkeypatch.setattr(SC, "candidates", lambda db, case, language: [{"code": "VIS_1", "kind": "place", "path": picture}])
    manifest = SV.make_covers(None, SimpleNamespace(id=9), "en", "The open door", {"C1": "Who opened it", "C2": "Why it stayed open"}, scale=0.1)
    assert sorted(manifest["covers"]) == ["long", "short_C1", "short_C2"] and manifest["safe_zones"] == "to_test"
    assert manifest["covers"]["short_C2"]["scene"]["reused"] is True
    saved = json.loads((tmp_path / "9" / "covers" / "en" / "manifest.json").read_text(encoding="utf-8"))
    assert saved["outfit_id"] == "en/navyBlazerBeigeChinos" and saved["platform_cover_ways"]["instagram_reel"] == "to_test"
    with pytest.raises(C.CoverError, match="6 short covers"):
        SV.make_covers(None, SimpleNamespace(id=9), "en", "The open door", {f"C{i}": "A title" for i in range(6)})
