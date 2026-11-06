import math

import numpy as np
import pytest

from app.core.ai_config import ai_config
from app.documentary.render import watermark as WM

W, H = 1920, 1080
LANGUAGES = ("fa", "ar", "de", "en")


def _cfg():
    return ai_config.branding_settings().watermark


def test_the_owner_settings_are_in_config():
    cfg = _cfg()
    assert (cfg.size_of_logo, cfg.opacity, cfg.circle_area_share) == (0.3333, 1.0, 0.28)
    assert not hasattr(cfg, "move_every_seconds"), "no jumps: it drifts all the time"


def test_the_circle_covers_the_configured_share_of_the_picture():
    r = WM.circle_radius(W, H, 0.28)
    assert math.pi * r * r == pytest.approx(0.28 * W * H)


def test_the_path_keeps_the_whole_logo_inside_the_circle_and_covers_the_length():
    w, h = 100, 40
    route = WM.path(W, H, w, h, 20000.0, _cfg(), "fa", 3600.0)
    r = WM.circle_radius(W, H, _cfg().circle_area_share)
    for x, y in route[::200]:
        for cx, cy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
            assert math.hypot(cx - W / 2, cy - H / 2) <= r + 1.5
    assert len(route) * WM.STEP >= 20000.0
    assert (route == WM.path(W, H, w, h, 20000.0, _cfg(), "fa", 3600.0)).all(), "the same film gets the same path"
    assert not (route[:100] == WM.path(W, H, w, h, 20000.0, _cfg(), "en", 3600.0)[:100]).all()


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_logo_is_a_third_of_the_corner_logo_and_drifts_slowly_and_evenly(language):
    mark = WM.Watermark({"language": language, "duration": 1000.0}, W, H)
    corner = round(H * ai_config.branding_settings().logo.height_fraction)
    assert mark.alpha.shape[0] == pytest.approx(corner / 3, abs=2)
    steps = [math.dist(mark.place_at(t / 25), mark.place_at((t + 1) / 25)) for t in range(0, 25 * 600, 7)]
    assert max(steps) < 0.3 and min(steps) > 0.05, "never a jump, never still"
    assert mark.speed * 60 < 1.5 * WM.circle_radius(W, H, 0.28), "very slow: under one and a half radii a minute"
    black = np.zeros((H, W, 3), np.uint8)
    out = mark.apply(black, 10.0)
    assert out.max() > 0.5 * 255, "the logo shows at full strength"
    assert not np.array_equal(out, mark.apply(black, 40.0)), "it has moved"


def test_a_language_without_a_logo_file_draws_nothing(monkeypatch):
    monkeypatch.setattr(WM, "logo_path", lambda language: None)
    frame = np.full((H, W, 3), 90, np.uint8)
    assert WM.Watermark({"language": "fa", "duration": 100.0}, W, H).apply(frame, 5.0) is frame
