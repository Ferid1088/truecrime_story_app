"""The panel beside a zoom-in map: the places the camera passes, lit by the zoom level the map has reached."""
import numpy as np

from app.core.ai_config import ai_config
from app.documentary.graphics import mappanel as PANEL
from app.documentary.visuals import maps as MAPS
from app.documentary.zoom_map import camera as CAM
from app.documentary.zoom_map import shot as ZS


def test_the_places_the_zoom_passes_come_from_the_geocoders_address_widest_first():
    geo = {"address": {"country": "United States", "state": "Ohio", "county": "Miami County", "town": "Tipp City"}}
    assert MAPS.step_names(geo, "Cunningham Court, Tipp City") == [
        "United States", "Ohio · Miami County", "Tipp City", "Cunningham Court"]
    assert MAPS.step_names({"address": {"country": "Germany"}}, "Berlin") == ["Germany", "Berlin"]
    assert MAPS.step_names({}, "The garage") == ["The garage"]


def test_each_place_becomes_current_at_its_zoom_level_and_the_last_at_the_closest():
    zooms = [-100.0, 4.5, 8.5, 12.0]
    assert PANEL.step_thresholds(4, zooms) == zooms
    assert PANEL.step_thresholds(3, zooms) == [-100.0, 8.5, 12.0] or PANEL.step_thresholds(3, zooms)[-1] == 12.0
    assert PANEL.step_thresholds(1, zooms) == [12.0] and PANEL.step_thresholds(2, zooms) == [-100.0, 12.0]
    assert [PANEL.current_step([0.0, 2.0, 5.0], t) for t in (0.0, 1.9, 2.0, 4.9, 5.0, 9.0)] == [0, 0, 1, 1, 2, 2]


def test_the_map_takes_its_share_of_the_frame_and_the_panel_the_rest():
    width = PANEL.map_width(1920)
    assert abs(width / 1920 - ai_config.zoom().panel["map_share"]) < 0.01 and width % 2 == 0
    attribution = ["\u00a9 OpenStreetMap contributors"]
    panel = {"names": ["United States", "Ohio", "Tipp City", "Cunningham Court"], "target": [39.96, -84.18],
             "camera": [{"t": t, "zoom": z, "lon": -84.18, "lat": 39.96, "pitch": 0.0, "bearing": 0.0}
                        for t, z in ((0.0, 2.0), (3.0, 6.0), (6.0, 10.0), (9.0, 15.0))]}
    map_frame = np.full((1080, width, 3), 90, np.uint8)
    times = [0.0, 3.0, 6.0, 9.0]
    en = PANEL.MapPanel("en", panel, "Cunningham Court, Ohio", attribution).compose(map_frame, 0.5, times, 1920)
    fa = PANEL.MapPanel("fa", panel, "Cunningham Court, Ohio", attribution).compose(map_frame, 0.5, times, 1920)
    assert en.shape == fa.shape == (1080, 1920, 3)
    assert (en[600:, :width] == 90).all() and (fa[600:, 1920 - width:] == 90).all()   # the map is mirrored with the page
    later = PANEL.MapPanel("en", panel, "Cunningham Court, Ohio", attribution).compose(map_frame, 9.5, times, 1920)
    assert not np.array_equal(en[:, width:], later[:, width:])                        # the lit place changed
    assert not np.array_equal(later[:600, :width], map_frame[:600])                   # level label and place card on the map
    assert np.array_equal(en[600:, :width], map_frame[600:])                          # the rest of the map is untouched


def test_the_map_names_its_level_and_scale_and_projects_the_marked_place():
    assert PANEL.level_keys(4) == list(PANEL.LEVELS) and PANEL.level_keys(2) == ["level_country", "level_place"]
    assert PANEL.level_keys(1) == ["level_place"]
    assert PANEL.scale_text(15.0, 40.0).startswith("1:") and PANEL.scale_text(3.0, 40.0).startswith("1:")
    camera = {"zoom": 12.0, "lon": -84.18, "lat": 39.96}
    assert PANEL.project(-84.18, 39.96, camera, 1248, 1080) == (624.0, 540.0)
    x, y = PANEL.project(-84.17, 39.97, camera, 1248, 1080)
    assert x > 624.0 and y < 540.0                                                   # east of centre, north is up


def test_a_zoom_shot_carries_the_places_the_camera_really_reaches(monkeypatch):
    plan = CAM.build_plan(ai_config.zoom(), "zoom_in", (39.96, -84.18), "street", 0)
    info = {"names": ["United States", "Ohio", "Tipp City", "Cunningham Court"]}
    times = ZS.step_times(plan, info, 1248, ai_config.render)
    reached = [t for t in times if t is not None]
    assert reached == sorted(reached) and times[0] == 0.0 and len(reached) >= 3


def test_a_long_credit_with_all_imagery_providers_still_fits_the_footer_in_every_language():
    attribution = ["© OpenStreetMap contributors", "Imagery: NASA Blue Marble / EOSDIS GIBS",
                   "Imagery: NASA Landsat WELD (2000 composite) / EOSDIS GIBS", "Imagery: U.S. Geological Survey"]
    panel = {"names": ["United States", "Ohio", "Tipp City", "Cunningham Court"], "target": [39.96, -84.18], "camera": []}
    for language in ("en", "de", "fa", "ar"):
        page = PANEL.MapPanel(language, panel, "Cunningham Court, Ohio", attribution).step(3)
        assert page.size[1] == 1080


def test_the_names_on_the_map_are_pure_white_with_a_black_border():
    width = PANEL.map_width(1920)
    panel = {"names": ["United States", "Ohio", "Tipp City", "Cunningham Court"], "target": [39.96, -84.18],
             "camera": [{"t": t, "zoom": z, "lon": -84.18, "lat": 39.96, "pitch": 0.0, "bearing": 0.0}
                        for t, z in ((0.0, 2.0), (3.0, 6.0), (6.0, 10.0), (9.0, 15.0))]}
    map_frame = np.full((1080, width, 3), 128, np.uint8)
    frame = PANEL.MapPanel("en", panel, "Cunningham Court, Ohio", []).compose(map_frame, 9.5, [0.0, 3.0, 6.0, 9.0], 1920)
    label = frame[40:110, 40:width // 2]                                   # the level and scale label, top left of the map
    assert (label == 255).all(axis=2).any(), "pure white letters"
    assert (label == 0).all(axis=2).any(), "with a black border"
    assert not ((label[..., 0] > 200) & (label[..., 1] > 100) & (label[..., 1] < 190) & (label[..., 2] < 80)).any(), "no amber"


def test_the_names_drawn_in_the_map_tiles_are_white_with_a_thick_black_border():
    style = ai_config.zoom().style
    assert style["label_color"] == "#ffffff" and style["label_halo"] == "#000000"
    assert style["label_halo_width"] >= 2.5 and style["label_scale"] >= 1.9


def test_a_persian_panel_has_persian_digits_and_each_credit_on_its_own_line_in_its_own_direction(monkeypatch):
    from app.documentary.graphics import mappanel as MP
    from app.documentary.graphics.page import Page

    assert MP.scale_text(14.0, 39.9, "fa").startswith("۱:") and MP.scale_text(14.0, 39.9).startswith("1:")
    drawn = []
    real = Page.text

    def spy(self, left, top, width, text, role, px, fill, **kw):
        drawn.append((text, kw.get("language")))
        return real(self, left, top, width, text, role, px, fill, **kw)

    monkeypatch.setattr(Page, "text", spy)
    credits = ["© OpenStreetMap contributors", "Imagery: U.S. Geological Survey"]
    panel = MP.MapPanel("fa", {"names": ["ایالات متحده", "تیپ سیتی"], "step_times": [0.0, 1.0]}, "تیپ سیتی", credits)
    panel.step(1)
    assert ("© OpenStreetMap contributors", "en") in drawn and ("Imagery: U.S. Geological Survey", "en") in drawn
    assert ("مرحله ۱", None) in drawn or any(t == "مرحله ۱" for t, _ in drawn)
    assert ("نقشه: داده‌های باز", "fa") in drawn


def test_the_marker_card_of_a_map_gets_the_place_in_the_films_language(monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    from app.documentary.mvp import evidence as EV
    from app.documentary.visuals import generated

    async def translate(db, case_id, items, language, narration_excerpt=""):
        return {k: f"fa:{v}" for k, v in items.items()}

    monkeypatch.setattr(generated, "localize_texts", translate)
    zoom = {"place": "Cunningham Court home", "names": ["United States", "Tipp City"], "names_language": "fa"}
    plan_row = SimpleNamespace(plan_json=json.dumps({"beats": [{"shots": [{"map_zoom": zoom}]}]}))
    db = SimpleNamespace(commit=lambda: None)
    asyncio.run(EV.localize_graphics(db, SimpleNamespace(id=1), plan_row, "fa"))
    saved = json.loads(plan_row.plan_json)["beats"][0]["shots"][0]["map_zoom"]
    assert saved["place_label"] == "fa:Cunningham Court home" and saved["place"] == "Cunningham Court home", \
        "the label is shown, the English place stays the geocoder's key"
    assert saved["names"] == ["United States", "Tipp City"], "the panel names were already localized: not done twice"
    asyncio.run(EV.localize_graphics(db, SimpleNamespace(id=1), plan_row, "fa"))
    assert json.loads(plan_row.plan_json)["beats"][0]["shots"][0]["map_zoom"]["place_label"] == "fa:Cunningham Court home"
