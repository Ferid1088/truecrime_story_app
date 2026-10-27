import numpy as np
import pytest

from app.core.ai_config import ai_config
from app.documentary.render import logo as LG

W, H = 1920, 1080


def _script(language="en", shots=None):
    shots = shots if shots is not None else [
        {"kind": "image", "start": 0.0, "end": 6.0}, {"kind": "video", "start": 6.0, "end": 10.0},
        {"kind": "graphic", "start": 10.0, "end": 20.0}, {"kind": "map", "start": 20.0, "end": 30.0},
        {"kind": "host", "start": 30.0, "end": 40.0}, {"kind": "image", "start": 40.0, "end": 42.0},
        {"kind": "image", "start": 50.0, "end": 58.0}]
    return {"language": language, "shots": shots}


def test_the_logo_shows_over_pictures_and_clips_only_and_never_in_a_stretch_shorter_than_the_minimum():
    windows = LG.windows_of(_script())["windows"]
    assert windows == [[0.0, 10.0], [50.0, 58.0]]        # pictures and clips joined; graphic, map, host, 2 s stub left out


def test_each_language_has_its_own_logo_file_and_a_film_without_one_has_no_logo(monkeypatch):
    paths = {lang: LG.logo_path(lang) for lang in ("en", "de", "fa", "ar")}
    assert all(p is not None and p.exists() for p in paths.values()) and len(set(paths.values())) == 4
    assert paths["fa"].name == "Logo_Persian.png" and paths["de"].name == "Logo_German.png"
    monkeypatch.setattr(LG, "logo_path", lambda language: None)
    assert LG.windows_of(_script()) == {"windows": []}
    script = {**_script(), "logo": {"windows": [[0.0, 10.0]]}}
    assert LG.LogoBug(script, W, H).windows == []


@pytest.mark.parametrize(("language", "right"), [("en", True), ("de", True), ("fa", False), ("ar", False)])
def test_the_logo_sits_in_the_top_corner_at_the_end_of_the_reading_direction(language, right):
    bug = LG.LogoBug({**_script(language), "logo": {"windows": [[0.0, 10.0]]}}, W, H)
    x0, y0, x1, y1 = bug.box
    assert y0 > 0 and y1 < H / 4
    assert (x1 > W * 0.9) if right else (x0 < W * 0.1)
    assert bug.rgb.shape[:2] == bug.alpha.shape[:2] == (y1 - y0, x1 - x0)


def test_only_the_corner_changes_only_inside_a_window_and_it_fades_in_and_out():
    bug = LG.LogoBug({**_script(), "logo": {"windows": [[2.0, 10.0]]}}, W, H)
    frame = np.full((H, W, 3), 120, np.uint8)
    assert np.array_equal(bug.apply(frame, 1.0), frame) and np.array_equal(bug.apply(frame, 10.5), frame)
    full = bug.apply(frame, 5.0)
    x0, y0, x1, y1 = bug.box
    changed = np.any(full != frame, axis=2)
    assert changed[y0:y1, x0:x1].any() and not changed[:y0].any() and not changed[y1:].any()
    assert not changed[:, :x0].any() and not changed[:, x1:].any()
    start = bug.apply(frame, 2.05)
    assert np.abs(start.astype(int) - frame).sum() < np.abs(full.astype(int) - frame).sum()   # still fading in
    assert not np.shares_memory(full, frame)                                                  # the input is untouched


def test_a_logo_file_without_transparency_loses_its_black_background():
    rgb, alpha = LG._bug(str(LG.logo_path("en")), 140, ai_config.branding_settings().logo.black_key_below, 0.0)
    assert alpha.shape == rgb.shape[:2] and alpha[:3].max() < 0.05 and alpha.max() > 0.9       # corners clear, logo solid
