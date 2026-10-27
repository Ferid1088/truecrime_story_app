"""The channel logo goes once around the screen every ten minutes of the film (15 seconds) and returns to its place."""
import numpy as np
import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.documentary.render import logo as LG

W, H = 1920, 1080


@pytest.fixture
def bug(tmp_path, monkeypatch):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 200, 0, 255)).save(path)
    monkeypatch.setattr(LG, "logo_path", lambda language: path)

    def make(language="en", duration=2000.0, windows=((10.0, 20.0),)):
        return LG.LogoBug({"language": language, "duration": duration, "logo": {"windows": [list(w) for w in windows]}},
                          W, H)
    return make


def test_the_lap_is_configured_as_every_ten_minutes_for_fifteen_seconds():
    lap = ai_config.branding_settings().logo.lap
    assert lap.every_seconds == 600 and lap.seconds == 15


def test_a_lap_starts_at_every_ten_minutes_but_not_at_the_start_and_only_when_it_ends_before_the_film(bug):
    b = bug(duration=1810.0)
    assert b.lap_at(0.5) is None and b.lap_at(599.9) is None and b.lap_at(615.0) is None
    assert b.lap_at(600.0) is not None and b.lap_at(614.9) is not None and b.lap_at(1200.0) is not None
    assert b.lap_at(1800.0) is None                                        # it would end at 1815 s, after the film


def test_the_logo_leaves_its_place_and_is_back_in_it_when_the_lap_ends(bug):
    b = bug()
    home = b.box[:2]
    assert b.position(0.0) == home and b.position(1.0) == home
    assert b.position(0.5) != home
    x, y, _ = b.lap_at(607.5)
    assert (x, y) != home


def test_the_lap_goes_clockwise_round_the_screen_above_the_subtitles(bug):
    b = bug()
    w = b.alpha.shape[1]
    margin = b.box[1]
    points = [b.position(n / 200) for n in range(201)]
    assert min(p[0] for p in points) == pytest.approx(margin) and max(p[0] for p in points) == pytest.approx(W - margin - w)
    assert min(p[1] for p in points) == pytest.approx(margin)
    lowest = max(p[1] for p in points) + b.alpha.shape[0]
    assert lowest == pytest.approx(H * (1 - ai_config.branding_settings().logo.lap.bottom_clear_fraction), abs=1.0)
    assert b.position(0.1)[0] == b.box[0] and b.position(0.1)[1] > b.box[1]       # top right: first down the right edge


def test_a_right_to_left_language_has_its_logo_and_lap_start_at_the_other_corner(bug):
    b = bug(language="fa")
    assert b.box[0] == b.box[1]                                          # top left
    assert b.position(0.1)[1] == b.box[1] and b.position(0.1)[0] > b.box[0]      # first along the top


def test_it_eases_out_of_and_into_its_place():
    assert LG.ease(0.0) == 0.0 and LG.ease(1.0) == 1.0 and LG.ease(0.1) < 0.1 / 2 and LG.ease(0.9) > 1 - 0.1 / 2


def test_during_a_lap_the_logo_is_drawn_where_it_is_even_over_a_graphic_and_back_to_normal_after(bug):
    b = bug()
    frame = np.zeros((H, W, 3), np.uint8)
    home = b.box
    assert not b.apply(frame, 300.0).any()                                # no window, no lap: nothing
    assert b.apply(frame, 15.0)[home[1]:home[3], home[0]:home[2]].any()   # inside a window: in its place
    x, y, _ = b.lap_at(607.5)
    drawn = b.apply(frame, 607.5)
    h, w = b.alpha.shape[:2]
    assert drawn[round(y):round(y) + h, round(x):round(x) + w].any()
    assert not drawn[home[1]:home[3], home[0]:home[2]].any()              # it is away from its corner
    assert not b.apply(frame, 620.0).any()


def test_a_lap_fades_in_and_out_only_where_the_logo_is_not_already_on_screen(bug):
    b = bug(windows=((595.0, 625.0),))
    assert b.lap_at(600.0)[2] == 0.0 and b.window_fade(600.0) == 1.0         # already shown: it stays
    plain = bug(windows=())
    assert plain.lap_at(600.0)[2] == 0.0 and plain.lap_at(607.0)[2] == 1.0 and plain.lap_at(614.9)[2] < 0.5
