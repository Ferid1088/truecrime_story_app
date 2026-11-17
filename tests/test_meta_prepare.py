# ruff: noqa: F811  (the `world` fixture is imported from test_publication_registry and used by name)
"""Non-destructive Reel copies: a clip with an edit list and keyframes too far apart (what the Short pipeline writes) gets a copy that passes Instagram's and Facebook's media
rules, with its audio bit for bit the same and its picture within the configured PSNR; the original is never touched. Generated clips only."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from test_publication_registry import _video, planned, pub, world  # noqa: F401

from app.core.media_tools import ffmpeg_bin
from app.meta_publishing.config import meta_config
from app.meta_publishing.copies import prepared_copy_of, prepared_path, sidecar_path
from app.meta_publishing.preflight import ReelsPreflight
from app.meta_publishing.prepare import PrepareError, ReelsPreparer
from app.meta_publishing.validator import Verdict

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is needed to make the test clips")


def clip(path: Path, *, seconds: int = 10, gop: int = 250, editlist: bool = True) -> Path:
    """A clip like the Short pipeline's: H.264 with B-frames and a long GOP, AAC, and (by default) the edit list ffmpeg writes for the encoder delays."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = [] if editlist else ["-use_editlist", "0"]
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=540x960:rate=30:duration={seconds}", "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={seconds}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-bf", "2", "-g", str(gop), "-sc_threshold", "0", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                    "-movflags", "+faststart", *flags, str(path)], check=True)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stream_md5(path: Path, kind: str) -> str:
    out = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", f"0:{kind}:0", "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True).stdout
    return out.strip()


def verdicts(path: Path) -> dict[str, str]:
    return {n: p.verdict.value for n, p in ReelsPreflight().check_file("T", path).platforms.items()}


@pytest.fixture
def preparer():
    return ReelsPreparer()


def test_the_pipelines_clips_fail_both_platforms_before_a_copy(tmp_path):
    original = clip(tmp_path / "clip_C1.mp4")
    assert verdicts(original) == {"instagram_reel": "FAIL", "facebook_reel": "FAIL"}
    report = ReelsPreflight().check_file("T", original)
    assert [c.name for c in report.platforms["instagram_reel"].by(Verdict.FAIL)] == ["no edit list"]
    assert "keyframe interval (GOP)" in [c.name for c in report.platforms["facebook_reel"].by(Verdict.FAIL)]


def test_a_copy_passes_both_platforms_keeps_the_audio_bit_for_bit_and_leaves_the_original_alone(tmp_path, preparer):
    original = clip(tmp_path / "clip_C1.mp4")
    before = sha(original)
    result = preparer.prepare(original)
    assert result.action == "reencode" and result.target == tmp_path / "meta" / "clip_C1.mp4" and result.target.is_file()
    assert sha(original) == before and result.source == original                                  # the original is byte-identical
    assert verdicts(result.target) == {"instagram_reel": "PASS", "facebook_reel": "PASS"}
    assert stream_md5(result.target, "a") == stream_md5(original, "a") and result.audio_identical is True
    assert result.psnr_db is not None and result.psnr_db >= meta_config().prepare.min_psnr_db
    facts = ReelsPreflight().prober.probe(result.target)
    assert facts.max_keyframe_gap is not None and facts.max_keyframe_gap <= meta_config().prepare.keyframe_seconds + 0.1
    assert facts.container.has_edit_list is False and facts.container.moov_before_mdat is True and facts.container.open_gop is False
    original_facts = ReelsPreflight().prober.probe(original)
    assert (facts.width, facts.height, facts.fps, facts.video_codec, facts.audio_codec, facts.audio_sample_rate) == (
        original_facts.width, original_facts.height, original_facts.fps, original_facts.video_codec, original_facts.audio_codec, original_facts.audio_sample_rate)
    assert abs(facts.duration - original_facts.duration) < 0.05


def test_every_frame_is_kept_none_repeated_or_dropped(tmp_path, preparer):
    original = clip(tmp_path / "clip_C1.mp4", seconds=6)
    copy = preparer.prepare(original).target

    def frames(path):
        return subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True).stdout.strip()

    assert frames(copy) == frames(original) == "180"


def test_only_the_container_is_rewritten_when_the_keyframes_are_fine(tmp_path, preparer):
    original = clip(tmp_path / "clip_C2.mp4", gop=60)
    assert verdicts(original)["facebook_reel"] != "FAIL"
    result = preparer.prepare(original)
    assert result.action == "remux" and verdicts(result.target) == {"instagram_reel": "PASS", "facebook_reel": "PASS"}
    assert stream_md5(result.target, "v") == stream_md5(original, "v") and stream_md5(result.target, "a") == stream_md5(original, "a")      # video and audio packets untouched
    assert result.psnr_db is None


def test_a_clip_that_already_meets_the_rules_gets_no_copy(tmp_path, preparer):
    original = clip(tmp_path / "clip_C3.mp4", gop=60, editlist=False)
    result = preparer.prepare(original)
    assert result.action == "none" and result.target is None and not (tmp_path / "meta").exists()
    assert prepared_copy_of(original) is None


def test_preparing_again_does_nothing_until_the_original_changes(tmp_path, preparer):
    original = clip(tmp_path / "clip_C1.mp4", seconds=6)
    first = preparer.prepare(original)
    stamp = first.target.stat().st_mtime_ns
    again = preparer.prepare(original)
    assert again.action == "up_to_date" and first.target.stat().st_mtime_ns == stamp and again.audio_identical is True
    clip(original, seconds=7)                                                                      # the clip was made again
    rebuilt = preparer.prepare(original)
    assert rebuilt.action == "reencode" and rebuilt.target.stat().st_mtime_ns != stamp
    assert preparer.prepare(original, force=True).action == "reencode"


def test_a_problem_a_copy_cannot_repair_is_reported_and_leaves_nothing_behind(tmp_path, preparer):
    short = clip(tmp_path / "clip_C4.mp4", seconds=1)
    with pytest.raises(PrepareError, match="duration .* cannot be repaired"):
        preparer.prepare(short)
    assert not (tmp_path / "meta").exists() or not list((tmp_path / "meta").iterdir())


def test_a_copy_whose_audio_differs_is_refused_and_removed(tmp_path, preparer, monkeypatch):
    original = clip(tmp_path / "clip_C1.mp4", seconds=6)
    before = sha(original)
    calls = {"n": 0}

    def digest(path):
        calls["n"] += 1
        return f"MD5={calls['n']}"

    monkeypatch.setattr(ReelsPreparer, "audio_digest", staticmethod(digest))
    with pytest.raises(PrepareError, match="audio differs"):
        preparer.prepare(original)
    assert sha(original) == before and prepared_copy_of(original) is None and not list((tmp_path / "meta").glob("*.part.mp4"))


def test_a_copy_too_far_from_the_original_is_refused(tmp_path, preparer, monkeypatch):
    original = clip(tmp_path / "clip_C1.mp4", seconds=6)
    monkeypatch.setattr(ReelsPreparer, "psnr", staticmethod(lambda a, b: 30.0))
    with pytest.raises(PrepareError, match="too far from the original"):
        preparer.prepare(original)
    assert prepared_copy_of(original) is None and not list((tmp_path / "meta").glob("*.part.mp4"))


def test_an_encoder_failure_is_reported_and_leaves_nothing_behind(tmp_path, preparer, monkeypatch):
    original = clip(tmp_path / "clip_C1.mp4", seconds=6)
    real = subprocess.run

    def broken(cmd, *a, **k):
        if "libx264" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
        return real(cmd, *a, **k)

    monkeypatch.setattr("app.meta_publishing.prepare.subprocess.run", broken)
    with pytest.raises(PrepareError, match="ffmpeg could not make the copy: boom"):
        preparer.prepare(original)
    assert prepared_copy_of(original) is None and not list((tmp_path / "meta").glob("*.part.mp4"))


def test_the_copy_can_never_replace_the_original(tmp_path, preparer, monkeypatch):
    original = clip(tmp_path / "clip_C1.mp4", seconds=6)
    monkeypatch.setattr(meta_config().instagram, "prepared_copy_dir", "")
    before = sha(original)
    with pytest.raises(PrepareError, match="would replace the original"):
        preparer.prepare(original)
    assert sha(original) == before and prepared_path(original) == original


def test_the_sidecar_records_what_was_done(tmp_path, preparer):
    import json

    original = clip(tmp_path / "clip_C1.mp4", seconds=6)
    result = preparer.prepare(original)
    kept = json.loads(sidecar_path(result.target).read_text())
    assert kept["source_sha256"] == sha(original) and kept["action"] == "reencode" and kept["audio_md5"] == stream_md5(result.target, "a")
    assert kept["parameters"] == meta_config().prepare.model_dump() and kept["verdicts"] == {"instagram_reel": "PASS", "facebook_reel": "PASS"}


# --- Instagram and Facebook use the copy: the registry, the preflight report and the publisher ---------------------------------------------------------------------------

def test_the_registry_gives_instagram_and_facebook_the_copy_and_the_rest_the_original(world, monkeypatch):
    planned(world)
    original = world.clips / "clip_C1.mp4"
    assert pub(world, "instagram", "short", "C1").video_path.endswith("clip_C1.mp4") and "/meta/" not in pub(world, "instagram", "short", "C1").video_path
    world.registry.approve_content(pub(world, "instagram", "short", "C1").id)
    world.registry.approve_publication(pub(world, "instagram", "short", "C1").id)
    first_hash = pub(world, "instagram", "short", "C1").content_hash
    _video(prepared_path(original), seconds=7)                                                     # a prepared copy appears (a different file here, to see the switch)
    planned(world)
    for platform in ("instagram", "facebook"):
        row = pub(world, platform, "short", "C1")
        assert "/meta/clip_C1.mp4" in row.video_path and row.content_hash != first_hash
    for platform in ("youtube", "tiktok"):
        row = pub(world, platform, "short", "C1")
        assert "/meta/" not in row.video_path and row.content_hash == first_hash
    assert pub(world, "instagram", "short", "C1").status == "voided"                              # the file it was approved with is no longer the file it would send
    view = world.registry.view(pub(world, "instagram", "short", "C1"))
    assert view["prepared_copy"] is True and view["file"].endswith("meta/clip_C1.mp4")
    assert world.registry.view(pub(world, "youtube", "short", "C1"))["prepared_copy"] is False


def test_the_preflight_report_judges_the_copy_when_there_is_one(world, monkeypatch):
    monkeypatch.setattr(meta_config().preflight, "case_id", world.case.id)
    original = world.clips / "clip_C2.mp4"
    _video(prepared_path(original), seconds=7)
    report = {r.clip_key: r for r in ReelsPreflight().check_case(world.db)}
    assert report["C2"].path == prepared_path(original) and report["C1"].path == world.clips / "clip_C1.mp4"
