"""The platforms' file rules (config/platform_specs.json), the check of a real video against them, and the delivery copy of a clip."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from app.core.ai_config import ai_config
from app.core.media_tools import ffmpeg_bin
from app.publishing import platform_specs as PS
from app.shortform import clip_video as CV
from app.shortform import delivery as D

needs_ffmpeg = pytest.mark.skipif(ffmpeg_bin() is None, reason="ffmpeg is not available")


def test_every_platform_names_its_sources_and_how_well_its_numbers_are_known():
    s = PS.specs()
    assert set(s.platforms) == {"facebook_reels", "instagram_reels", "tiktok", "youtube_shorts"} and s.verified_on
    for key, p in s.platforms.items():
        assert p.sources and all(u.startswith("https://") for u in p.sources), key
        assert p.video.status and "status" in p.cover, key
    assert set(s.cover_use) == {"reel", "feed", "grid"}
    with pytest.raises(PS.SpecError):
        PS.platform("myspace")


def test_the_official_meta_numbers_in_the_config_are_the_ones_the_encode_is_made_to():
    limits = PS.platform("facebook_reels").video
    delivery = ai_config.short_clips_settings().video.delivery
    assert (limits.width, limits.height, limits.fps_min, limits.fps_max) == (1080, 1920, 24, 60)
    assert limits.gop_seconds_min <= delivery.gop_seconds <= limits.gop_seconds_max
    assert limits.audio_rate == 48000 and limits.audio_channels == 2 and limits.audio_kbps_min <= int(delivery.audio_bitrate.rstrip("k"))


def _source(path: Path, size: str, gop: int, volume: float, rate: int = 44100, seconds: int = 4) -> Path:
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}", "-af", f"volume={volume},alimiter=limit=1.0",
                    "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop), "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


@needs_ffmpeg
def test_a_file_made_for_the_platforms_passes_every_check_and_a_bad_one_is_told_what_is_wrong(tmp_path):
    cfg = ai_config.short_clips_settings().video.model_copy(update={"preset": "ultrafast"})
    bad = _source(tmp_path / "bad.mp4", "216x384", 250, 6.0, seconds=9)
    good = tmp_path / "good.mp4"
    CV.assemble(CV.delivery_command(_source(tmp_path / "wide.mp4", "540x960", 250, 6.0, seconds=8), good, cfg))
    for key in ("facebook_reels", "instagram_reels", "tiktok", "youtube_shorts"):
        assert PS.check_video(good, key) == [], key
    problems = " | ".join(PS.check_video(bad, "facebook_reels"))
    assert "at least 540 are needed" in problems and "key frames" in problems and "peaks at" in problems and "44100 Hz" in problems
    assert PS.check_video(bad, "tiktok") and "key frames" not in " ".join(PS.check_video(bad, "tiktok"))      # TikTok's page names no GOP rule


@needs_ffmpeg
def test_the_delivery_copy_sits_beside_the_original_is_rebuilt_when_the_original_is_newer_and_never_replaces_it(tmp_path):
    original = _source(tmp_path / "clip_C1.mp4", "540x960", 250, 6.0, seconds=7)
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    assert D.delivery_path(original).name == "delivery_C1.mp4" and D.delivery_of(original) is None and D.delivery_of(None) is None
    copy = D.ensure_delivery(original)
    assert copy.is_file() and D.delivery_of(original) == copy and hashlib.sha256(original.read_bytes()).hexdigest() == before
    assert PS.check_video(copy, "facebook_reels") == [] and not list(tmp_path.glob("*.part.mp4"))
    first = copy.stat().st_mtime
    assert D.ensure_delivery(original) == copy and copy.stat().st_mtime == first                         # nothing is made twice
    os.utime(copy, (first - 100, first - 100))                                                              # the clip was assembled again after this copy was made
    assert D.delivery_of(original) is None
    assert D.ensure_delivery(original).stat().st_mtime > first - 50 and D.delivery_of(original) is not None


def test_a_missing_clip_is_reported_and_nothing_is_made(tmp_path):
    with pytest.raises(CV.ClipVideoError, match="does not exist"):
        D.ensure_delivery(tmp_path / "clip_C9.mp4")
    assert not list(tmp_path.iterdir())


def test_publishing_sends_a_shorts_delivery_copy_to_every_platform_and_otherwise_what_it_sent_before(tmp_path):
    from types import SimpleNamespace

    from app.publishing.registry import Kind
    from app.publishing.registry import PublicationRegistry as R

    original = tmp_path / "clip_C1.mp4"
    original.write_bytes(b"original")
    asset = SimpleNamespace(kind=Kind.SHORT, path=original)
    assert R._path_for(asset, "tiktok") == original and R._path_for(asset, "youtube") == original
    copy = D.delivery_path(original)
    copy.write_bytes(b"delivery")
    assert all(R._path_for(asset, platform) == copy for platform in ("instagram", "facebook", "tiktok", "youtube"))
    assert R._path_for(SimpleNamespace(kind=Kind.FILM, path=original), "youtube") == original        # a film is not a clip: no delivery copy
    assert D.delivery_of(tmp_path / "clip_C9.mp4") is None


@needs_ffmpeg
def test_check_all_measures_once_and_reports_every_platform(tmp_path):
    cfg = ai_config.short_clips_settings().video.model_copy(update={"preset": "ultrafast"})
    good = tmp_path / "good.mp4"
    CV.assemble(CV.delivery_command(_source(tmp_path / "wide.mp4", "540x960", 250, 6.0, seconds=8), good, cfg))
    assert PS.check_all(good) == {"facebook_reels": [], "instagram_reels": [], "tiktok": [], "youtube_shorts": []}


@needs_ffmpeg
def test_a_delivery_copy_has_no_edit_list_and_the_index_at_the_front_as_instagram_asks(tmp_path):
    from app.meta_publishing.preflight import ReelsPreflight
    from app.meta_publishing.validator import Verdict

    cfg = ai_config.short_clips_settings().video.model_copy(update={"preset": "ultrafast"})
    out = tmp_path / "delivery_C1.mp4"
    CV.assemble(CV.delivery_command(_source(tmp_path / "wide.mp4", "540x960", 250, 6.0, seconds=8), out, cfg))
    report = ReelsPreflight().check_file("C1", out)
    failed = [c.name for c in report.platforms["instagram_reel"].by(Verdict.FAIL)]
    assert "no edit list" not in failed and "moov atom at the front" not in failed


@needs_ffmpeg
def test_a_delivery_copy_with_a_cover_is_made_once_and_made_again_only_when_the_cover_changes(tmp_path):
    from PIL import Image

    original = _source(tmp_path / "clip_C1.mp4", "540x960", 250, 6.0, seconds=5)
    red, blue = tmp_path / "red.jpg", tmp_path / "blue.jpg"
    Image.new("RGB", (540, 960), (250, 10, 10)).save(red)
    Image.new("RGB", (540, 960), (10, 10, 250)).save(blue)
    plain = D.ensure_delivery(original)
    assert D.cover_of(plain) is None
    copy = D.ensure_delivery(original, cover=red)
    first = copy.stat().st_mtime
    assert D.cover_of(copy) is not None and D.ensure_delivery(original, cover=red).stat().st_mtime == first       # the same cover: nothing is made again
    assert D.ensure_delivery(original, cover=blue).stat().st_mtime > first and D.cover_of(copy) != D.cover_of(red)
    assert PS.check_video(copy, "facebook_reels") == [] and not list(tmp_path.glob("*.part.mp4"))
