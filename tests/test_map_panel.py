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
    names, attribution = ["United States", "Ohio", "Tipp City", "Cunningham Court"], ["© OpenStreetMap contributors"]
    map_frame = np.full((1080, width, 3), 90, np.uint8)
    en = PANEL.MapPanel("en", names, attribution).compose(map_frame, 0.5, [0.0, 3.0, 6.0, 9.0], 1920)
    fa = PANEL.MapPanel("fa", names, attribution).compose(map_frame, 0.5, [0.0, 3.0, 6.0, 9.0], 1920)
    assert en.shape == fa.shape == (1080, 1920, 3)
    assert (en[:, :width] == 90).all() and (fa[:, 1920 - width:] == 90).all()      # the map is mirrored with the page
    later = PANEL.MapPanel("en", names, attribution).compose(map_frame, 9.5, [0.0, 3.0, 6.0, 9.0], 1920)
    assert not np.array_equal(en[:, width:], later[:, width:])                        # the lit place changed
    assert np.array_equal(later[:, :width], map_frame)                                # the map itself is untouched


def test_a_zoom_shot_carries_the_places_the_camera_really_reaches(monkeypatch):
    plan = CAM.build_plan(ai_config.zoom(), "zoom_in", (39.96, -84.18), "street", 0)
    info = {"names": ["United States", "Ohio", "Tipp City", "Cunningham Court"]}
    times = ZS.step_times(plan, info, 1248, ai_config.render)
    reached = [t for t in times if t is not None]
    assert reached == sorted(reached) and times[0] == 0.0 and len(reached) >= 3
