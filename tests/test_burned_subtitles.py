"""The film's subtitles are drawn into the picture besides the subtitle stream; case 15 keeps a picture without them."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from PIL import Image
from test_visual_production import _jpg, needs_ffmpeg

from app.core.ai_config import ai_config
from app.documentary.render.engine import Overlays, VideoRenderer

SUBTITLES = [{"start": 0.2, "end": 2.0, "text": "It was two thirty-one in the morning."}]


def test_the_config_burns_subtitles_for_every_case_except_the_listed_ones():
    render = ai_config.render
    assert render.burn_subtitles is True
    assert render.burn_subtitles_except_cases == [15]
    assert render.burns_subtitles(15) is False
    assert render.burns_subtitles(16) is True
    assert render.burns_subtitles(51) is True


def test_overlays_draw_a_subtitle_into_the_frame_only_when_burning():
    script = {"language": "en", "overlays": [], "subtitles": SUBTITLES}
    frame = np.zeros((180, 320, 3), np.uint8)
    assert Overlays(script, 320, 180, True).apply(frame, 1.0).any()
    assert not Overlays(script, 320, 180, False).apply(frame, 1.0).any()
    assert not Overlays(script, 320, 180, True).apply(frame, 3.0).any()


def _frame_at(film, seconds, tmp_path, name):
    png = tmp_path / f"{name}.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(seconds), "-i", str(film), "-frames:v", "1", str(png)], check=True)
    return np.asarray(Image.open(png).convert("RGB")).astype(float)


def _difference(a, b):
    height = a.shape[0]
    return float(np.abs(a - b)[height * 3 // 5:].mean())       # the subtitle sits at the bottom of the picture


@needs_ffmpeg
def test_a_rendered_film_shows_its_subtitle_in_the_picture_when_burning_is_asked_for(tmp_path):
    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", str(wav)], check=True)
    picture = _jpg(tmp_path / "a.jpg")
    script = {"language": "en", "duration": 3.0, "audio": {"path": str(wav)}, "overlays": [], "subtitles": SUBTITLES,
              "shots": [{"index": 0, "start": 0, "end": 3.0, "kind": "image", "path": str(picture), "width": 800, "height": 500,
                         "motion": "NONE", "transition_in": "CROSSFADE"}]}
    burned, plain = tmp_path / "burned.mp4", tmp_path / "plain.mp4"
    VideoRenderer(320, 180).render(script, burned, burn_subtitles=True)
    VideoRenderer(320, 180).render(script, plain, burn_subtitles=False)
    with_subtitle = _difference(_frame_at(burned, 1.2, tmp_path, "b1"), _frame_at(plain, 1.2, tmp_path, "p1"))
    without_subtitle = _difference(_frame_at(burned, 2.6, tmp_path, "b2"), _frame_at(plain, 2.6, tmp_path, "p2"))
    assert with_subtitle > 4 * max(without_subtitle, 0.1)
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(burned)],
                             capture_output=True, text=True).stdout.split()
    assert streams == ["video", "audio", "subtitle"]


def test_no_film_is_rendered_without_subtitles(tmp_path):
    from app.documentary.render.engine import RenderError

    script = {"language": "en", "duration": 3.0, "audio": {"path": str(tmp_path / "a.wav")}, "overlays": [], "subtitles": [],
              "shots": [{"index": 0, "start": 0, "end": 3.0, "kind": "black", "motion": "NONE", "transition_in": "CROSSFADE"}]}
    with pytest.raises(RenderError, match="no film is made without them"):
        VideoRenderer(320, 180).render(script, tmp_path / "out.mp4")
    script["subtitles"] = [{"start": 5.0, "end": 6.0, "text": "after the end"}]
    with pytest.raises(RenderError, match="no film is made without them"):
        VideoRenderer(320, 180).render(script, tmp_path / "out.mp4")
