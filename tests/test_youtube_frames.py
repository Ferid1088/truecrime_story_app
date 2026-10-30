"""Photos from the scenes of a video: sharp, distinct, spread over the video, at most the configured number."""
from __future__ import annotations

import shutil
import subprocess

import pytest
from PIL import Image

from app.core.ai_config import ai_config
from app.documentary.visuals import youtube_frames as YF

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is needed")


def _video(tmp_path, colors, seconds=6.0, noise=60):
    """A video of still scenes: each scene is one sharp textured picture (fixed noise) in a colour, `seconds` long."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for n, color in enumerate(colors):
        base = Image.effect_noise((640, 360), noise).convert("RGB")
        tint = Image.new("RGB", (640, 360), color)
        Image.blend(base, tint, 0.45).save(tmp_path / f"s{n}.png")
        cmd += ["-loop", "1", "-t", str(seconds), "-framerate", "25", "-i", str(tmp_path / f"s{n}.png")]
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(colors))) + f"concat=n={len(colors)}:v=1:a=0[v]",
            "-map", "[v]", "-pix_fmt", "yuv420p", str(tmp_path / "v.mp4")]
    subprocess.run(cmd, check=True, timeout=120)
    return tmp_path / "v.mp4"


def test_a_sharp_picture_scores_higher_than_a_blurred_one_and_black_bars_are_seen():
    sharp = Image.effect_noise((320, 180), 80).convert("RGB")
    blurred = sharp.resize((8, 4)).resize((320, 180))
    assert YF.sharpness_of(sharp) > YF.sharpness_of(blurred) * 20
    barred = Image.new("RGB", (320, 180), (120, 120, 120))
    barred.paste((0, 0, 0), (0, 0, 320, 20))
    barred.paste((0, 0, 0), (0, 160, 320, 180))
    assert YF.has_bars(barred) and not YF.has_bars(sharp)
    assert YF.sampled_times(10.0, 4) == [1.25, 3.75, 6.25, 8.75]


@needs_ffmpeg
def test_one_good_frame_is_taken_for_each_scene_and_never_more_than_the_limit(tmp_path):
    video = _video(tmp_path, ["red", "blue", "green", "orange", "purple"], seconds=6.0)
    frames = YF.frames_of(video, tmp_path / "frames", limit=3)
    assert 1 <= len(frames) <= 3 and all(f.path.exists() and f.sharpness >= ai_config.youtube_frames.min_sharpness
                                         for f in frames)
    assert [f.at for f in frames] == sorted(f.at for f in frames)
    assert YF.frames_of(video, tmp_path / "frames2", limit=0) == []
    assert len({f.path for f in YF.frames_of(video, tmp_path / "frames3")}) <= ai_config.youtube_frames.max_per_identity


@needs_ffmpeg
def test_a_frame_that_is_too_dark_or_looks_like_one_already_kept_is_left_out(tmp_path):
    dark = _video(tmp_path, ["black", "black"], seconds=6.0, noise=1)
    assert YF.frames_of(dark, tmp_path / "dark") == []
    same = _video(tmp_path, ["red"], seconds=30.0)
    kept = YF.frames_of(same, tmp_path / "same")
    assert len(kept) <= 2, "the frames of one unchanging picture count as one picture"


@needs_ffmpeg
def test_frames_become_photo_candidates_of_a_case_with_the_owners_rights_decision_and_never_more_than_twenty_an_identity(
        db_session, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from app.db.models import Case, VisualAsset
    from app.documentary import storage

    monkeypatch.setattr(storage, "cases_root", lambda: tmp_path / "cases")
    case = Case(canonical_title="Frames", slug=f"frames-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    video = SimpleNamespace(url="https://www.youtube.com/watch?v=x", title="A news report", channel_name="A station")
    clip = _video(tmp_path, ["red", "blue", "green", "orange", "purple"], seconds=6.0)
    frames = YF.frames_of(clip, tmp_path / "frames", limit=4)
    made = YF.register_frames(db_session, case, video, frames, "the courthouse", "A news report (YouTube)", "ab" * 32)
    assert len(made) == len(frames) >= 1
    first = made[0]
    assert (first.provider, first.asset_type, first.rights_status, first.found_for) == (
        "youtube_frame", "photo", "editorial_review_required", "the courthouse")
    assert first.source_url == video.url and first.verification_status == "unverified" and "A news report" in first.caption
    monkeypatch.setattr(ai_config.youtube_frames, "max_per_identity", len(frames))
    assert YF.register_frames(db_session, case, video, frames, "the courthouse", "x", "ab" * 32) == [], "the identity is full"
    assert len(YF.register_frames(db_session, case, video, frames, "the judge", "x", "ab" * 32)) >= 1, "another identity"
    assert db_session.query(VisualAsset).filter_by(case_id=case.id, provider="youtube_frame").count() >= 2
