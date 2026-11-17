"""Offline Meta Reels preflight: real tiny videos made with ffmpeg in a temp folder; no network, no database, no credentials."""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.meta_publishing.config import meta_config
from app.meta_publishing.preflight import ReelsPreflight
from app.meta_publishing.probe import ProbeError, Prober
from app.meta_publishing.validator import Verdict

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is needed to make the test videos")
IG, FB = "instagram_reel", "facebook_reel"


def _make(path: Path, *, size="540x960", seconds=5, fps=30, vcodec="libx264", audio=True, gop=60, extra=()) -> Path:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
    cmd += ["-c:v", vcodec, "-pix_fmt", "yuv420p", "-g", str(gop), "-sc_threshold", "0", "-use_editlist", "0", "-movflags", "+faststart"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"] if audio else ["-an"]
    subprocess.run([*cmd, *extra, str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def folder(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("meta")


@pytest.fixture(scope="module")
def good(folder) -> Path:
    return _make(folder / "good.mp4")


def _run(path: Path):
    return ReelsPreflight().check_file("T", path)


def test_a_valid_clip_passes_on_both_platforms(good):
    r = _run(good)
    assert r.error is None
    assert r.verdict(IG) is Verdict.PASS, [c for c in r.platforms[IG].checks if c.verdict is not Verdict.PASS]
    assert r.verdict(FB) is Verdict.PASS, [c for c in r.platforms[FB].checks if c.verdict is not Verdict.PASS]


def test_the_check_never_changes_the_file(good):
    before = (good.stat().st_mtime_ns, good.read_bytes())
    _run(good)
    assert (good.stat().st_mtime_ns, good.read_bytes()) == before


def test_a_long_keyframe_gap_fails_facebook_only_and_asks_for_a_re_encode(folder):
    r = _run(_make(folder / "longgop.mp4", gop=250, seconds=12))
    assert r.verdict(FB) is Verdict.FAIL
    assert r.verdict(IG) is Verdict.PASS
    action = " ".join(r.platforms[FB].actions())
    assert "Re-encode" in action and "cannot move keyframes" in action


def test_an_unsupported_video_codec_fails(folder):
    r = _run(_make(folder / "mpeg4.mp4", vcodec="mpeg4"))
    assert r.verdict(IG) is Verdict.FAIL and r.verdict(FB) is Verdict.FAIL
    assert any(c.name == "video codec" and c.verdict is Verdict.FAIL for c in r.platforms[IG].checks)


def test_a_clip_without_audio_fails(folder):
    r = _run(_make(folder / "silent.mp4", audio=False))
    assert r.verdict(IG) is Verdict.FAIL and r.verdict(FB) is Verdict.FAIL
    assert any(c.name == "audio stream" for c in r.platforms[FB].checks if c.verdict is Verdict.FAIL)


def test_a_landscape_clip_fails_facebook_and_warns_instagram(folder):
    r = _run(_make(folder / "wide.mp4", size="960x540"))
    assert r.verdict(FB) is Verdict.FAIL
    ig = r.platforms[IG]
    assert ig.verdict is Verdict.WARNING          # Instagram accepts the ratio (0.01 to 10) but 9:16 is only recommended
    assert any(c.name == "aspect ratio 9:16" and c.level == "recommended" for c in ig.by(Verdict.WARNING))


def test_a_too_short_clip_fails_both(folder):
    r = _run(_make(folder / "short.mp4", seconds=1))
    assert r.verdict(IG) is Verdict.FAIL and r.verdict(FB) is Verdict.FAIL


def test_a_clip_over_ninety_seconds_fails_facebook_but_not_instagram(folder):
    r = _run(_make(folder / "long.mp4", size="108x192", seconds=95, fps=24, extra=("-b:v", "50k")))
    assert r.verdict(FB) is Verdict.FAIL
    assert any(c.name == "duration" and c.verdict is Verdict.FAIL for c in r.platforms[FB].checks)
    assert all(c.name != "duration" or c.verdict is Verdict.PASS for c in r.platforms[IG].checks)


def test_a_frame_rate_below_the_minimum_fails(folder):
    r = _run(_make(folder / "slow.mp4", fps=15, gop=30))
    assert any(c.name == "frame rate" and c.verdict is Verdict.FAIL for c in r.platforms[IG].checks)


def test_moov_at_the_end_is_reported(folder):
    path = folder / "tail.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=4", "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-use_editlist", "0", str(path)], check=True)
    assert any(c.name == "moov atom at the front" and c.verdict is Verdict.FAIL for c in _run(path).platforms[IG].checks)


def test_an_edit_list_fails_instagram(folder):
    path = folder / "elst.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=4", "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(path)], check=True)
    r = _run(path)
    assert any(c.name == "no edit list" and c.verdict is Verdict.FAIL for c in r.platforms[IG].checks)
    assert all(c.verdict is not Verdict.FAIL for c in r.platforms[FB].checks if c.name == "no edit list")   # Meta's Facebook page does not state the rule


def test_a_text_file_is_an_error_not_a_pass(folder):
    path = folder / "notvideo.mp4"
    path.write_text("this is not a video")
    r = _run(path)
    assert r.error and not r.platforms
    assert r.verdict(IG) is Verdict.FAIL


def test_a_missing_file_is_an_error(folder):
    assert "file not found" in (_run(folder / "nothing.mp4").error or "")


def test_a_clip_without_a_recorded_file_is_an_error():
    assert "no video file" in (ReelsPreflight().check_file("T", None).error or "")


def test_ffprobe_failure_is_reported(good):
    def broken(args, timeout):
        raise ProbeError("ffprobe failed: boom")

    r = ReelsPreflight(runner=broken).check_file("T", good)
    assert r.error == "ffprobe failed: boom" and not r.platforms


def test_ffprobe_garbage_output_is_reported(good):
    r = ReelsPreflight(runner=lambda args, timeout: "not json").check_file("T", good)
    assert "not JSON" in (r.error or "")


def test_ffprobe_not_installed_is_reported(good, monkeypatch):
    monkeypatch.setattr("app.meta_publishing.probe.ffprobe_bin", lambda: None)
    assert "not available" in (ReelsPreflight().check_file("T", good).error or "")


def test_ffprobe_timeout_is_reported(good, monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("ffprobe", 1)

    monkeypatch.setattr("app.meta_publishing.probe.subprocess.run", timeout)
    monkeypatch.setattr("app.meta_publishing.probe.ffprobe_bin", lambda: "/usr/bin/ffprobe")
    assert "timed out" in (ReelsPreflight().check_file("T", good).error or "")


def test_every_rule_has_an_evidence_level():
    required = {"containers", "video_codecs", "pix_fmts", "audio_codecs", "audio_channels", "duration", "max_bytes", "fps", "max_width", "aspect_range", "audio_sample_rate",
                "video_bitrate", "recommended_aspect", "resolution_floor", "audio_bitrate", "gop", "closed_gop", "faststart", "edit_list"}
    for spec in meta_config().platforms.values():
        assert required <= set(spec.levels)


def test_prober_reads_the_facts(good):
    f = Prober(30).probe(good)
    assert (f.width, f.height, f.video_codec, f.audio_codec, f.audio_sample_rate) == (540, 960, "h264", "aac", 48000)
    assert f.container.moov_before_mdat is True and f.container.has_edit_list is False and f.container.open_gop is False
    assert f.max_keyframe_gap is not None and f.max_keyframe_gap <= 2.5
