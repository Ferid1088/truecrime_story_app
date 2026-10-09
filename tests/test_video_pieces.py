"""Video pieces: a video is kept whole (muted) and cut at its scene changes
into described pieces; a sentence shows only its piece, overlapping
pieces never both appear, and every piece is judged as video."""
import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.visuals import pieces as PC
from app.documentary.visuals.usage import UsageTracker, asset_facts
from app.providers.generation.base import GenerationResult

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    return tmp_path


def _case(db) -> Case:
    title = f"Pieces {uuid.uuid4().hex[:6]}"
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


def _three_scenes(path: Path, seconds=(6, 6, 30)) -> Path:
    """Three visibly different scenes (red, test pattern, blue) + sound."""
    a, b, c = seconds
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=red:size=640x360:rate=25:duration={a}",
        "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=25:duration={b}",
        "-f", "lavfi", "-i", f"color=c=blue:size=640x360:rate=25:duration={c}",
        "-f", "lavfi", "-i", f"sine=frequency=500:duration={a + b + c}",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


# ---------------------------------------------------------------------------
# cutting
# ---------------------------------------------------------------------------


def test_pieces_follow_the_scenes_and_overlap_only_inside_long_ones():
    # scenes 0–6, 6–6.8 (a flash), 6.8–12, 12–42
    got = PC.plan_pieces(42.0, [6.0, 6.8, 12.0], min_s=4, max_s=20, overlap=2, max_pieces=12)
    assert got[0] == (0.0, 6.0)
    assert got[1] == (6.0, 12.0)               # the flash joined its scene
    long = [p for p in got if p[0] >= 12.0]
    assert long[0] == (12.0, 32.0) and long[1][0] == 30.0 and long[-1][1] == 42.0
    assert all(b - a >= 4 for a, b in got)
    # never more than max_pieces, spread over the whole video
    many = PC.plan_pieces(600.0, [float(x) for x in range(5, 600, 5)], max_pieces=6)
    assert len(many) == 6 and many[-1][1] > 400


@needs_ffmpeg
def test_scene_changes_are_detected(env):
    v = _three_scenes(env / "src.mp4")
    cuts = PC.detect_cuts(v)
    assert any(abs(c - 6) < 0.3 for c in cuts) and any(abs(c - 12) < 0.3 for c in cuts)


@needs_ffmpeg
def test_a_video_is_kept_whole_and_cut_into_described_pieces(db_session, env):
    case = _case(db_session)
    v = _three_scenes(env / "src.mp4")
    source, pieces = PC.ingest_video(db_session, case, v, {
        "provider": "upload", "rights_status": "owned", "asset_role": "evidence",
        "title": "Search at the lake", "entities": ["the_lake"], "entity_key": "the_lake",
        "found_during": "upload"})
    assert source.asset_type == "video_source" and source.verification_status == "source"
    from app.documentary.visuals.footage import probe_video, sheet_path

    info = probe_video(storage.resolve(source.local_path))
    assert info["has_video"] and not info["has_audio"] and info["duration"] == pytest.approx(42, abs=0.2)
    windows = [(p.clip_start, p.clip_end) for p in pieces]
    assert windows[0][1] == pytest.approx(6, abs=0.3) and windows[-1][1] == pytest.approx(42, abs=0.2)
    for p in pieces:
        assert p.asset_type == "video" and p.local_path == source.local_path
        assert p.verification_status == "unverified"
        assert PC.piece_info(p)["parent"] == source.asset_code
        assert storage.resolve(p.thumbnail_path).exists()
        assert sheet_path(case.id, p.asset_code).exists()
        assert json.loads(p.entities_json) == ["the_lake"]
    # frames of a piece, in order, about one per second
    frames = PC.piece_frames(pieces[0])
    assert 5 <= len(frames) <= 7 and all(f[:2] == b"\xff\xd8" for f in frames)


# ---------------------------------------------------------------------------
# no footage twice
# ---------------------------------------------------------------------------


def _piece(code, parent, a, b):
    return VisualAsset(asset_code=code, asset_type="video", asset_role="context",
                       relevance_tier=3, clip_start=a, clip_end=b,
                       spec_json=json.dumps({"parent": parent, "window": [a, b]}))


def test_overlapping_pieces_are_never_both_shown():
    p1, p2, p3 = _piece("P1", "SRC", 12, 32), _piece("P2", "SRC", 30, 42), _piece("P3", "SRC", 0, 6)
    other = _piece("Q1", "OTHER", 30, 42)
    tr = UsageTracker({a.asset_code: asset_facts(a) for a in (p1, p2, p3, other)})
    tr.add("P1", 0, 8)
    assert tr.overlaps_shown("P2") and not tr.overlaps_shown("P3")
    assert not tr.overlaps_shown("Q1")                  # another video
    choice = tr.pick(["P2", "P3"], 10, 18, allow_repeat=False)
    assert choice["asset_id"] == "P3"
    assert tr.allows("P2", 20, 28)[0] is False


# ---------------------------------------------------------------------------
# the video auditor
# ---------------------------------------------------------------------------


class Gen:
    def __init__(self, answer):
        self.answer, self.seen = answer, []

    async def generate_structured(self, role, system, user, images=None):
        self.seen.append((role, json.loads(user), len(images or [])))
        return dict(self.answer), GenerationResult(text="{}", model="m/video", provider="fake")


def _describe(db, case, piece, answer):
    from app.documentary.visuals.video_auditor import VideoAuditor

    gen = Gen(answer)
    asyncio.run(VideoAuditor(gen=gen).describe(
        db, case, piece, [{"key": "the_lake", "name": "the lake", "type": "place"}], []))
    return gen


@needs_ffmpeg
def test_the_video_auditor_names_describes_and_checks_every_frame(db_session, env):
    case = _case(db_session)
    _, pieces = PC.ingest_video(db_session, case, _three_scenes(env / "v.mp4"), {
        "provider": "internet_archive", "rights_status": "public_domain",
        "asset_role": "context", "title": "lake film", "found_during": "research"})
    ok = {"name": "Divers at the lake shore", "description": "Police divers, daytime.",
          "subject_type": "event", "matches_claim": "yes", "role": "context",
          "entities": ["the_lake", "unknown"], "period_ok": "yes", "tone_ok": True,
          "text_or_logo_in_any_frame": False, "graphic_or_sensitive_in_any_frame": False,
          "quality": 0.8, "confidence": 0.9}
    gen = _describe(db_session, case, pieces[0], ok)
    role, payload, n_frames = gen.seen[0]
    assert role == "video_auditor" and n_frames >= 5 and "in order" in payload["frames"]
    p = pieces[0]
    assert p.title == "Divers at the lake shore" and p.description == "Police divers, daytime."
    assert p.verification_status == "verified" and "the_lake" in p.entities_json
    assert "unknown" not in p.entities_json
    # one frame with a TV logo or a caption fails the whole piece
    _describe(db_session, case, pieces[1], {**ok, "text_or_logo_in_any_frame": True})
    assert pieces[1].verification_status == "rejected"
    _describe(db_session, case, pieces[2], {**ok, "graphic_or_sensitive_in_any_frame": True})
    assert pieces[2].verification_status == "rejected"


@needs_ffmpeg
def test_placement_fails_when_a_frame_fails(db_session, env):
    from app.documentary.visuals.auditor import VisualAuditor

    case = _case(db_session)
    _, pieces = PC.ingest_video(db_session, case, _three_scenes(env / "w.mp4"), {
        "provider": "upload", "rights_status": "owned", "asset_role": "evidence",
        "title": "lake", "found_during": "upload"})
    p = pieces[0]
    p.verification_status = "verified"
    db_session.commit()
    gen = Gen({"verdict": "approved", "as": "evidence", "fits_words": 0.9,
               "specific_kind_ok": True, "tone_ok": True, "person_ok": True,
               "every_frame_ok": False, "problem_frames": [4], "reasons": ["a logo at the end"]})
    verdict, _, reasons = asyncio.run(VisualAuditor(gen=gen).check(
        db_session, case, p, ["Divers searched the lake for three days."]))
    assert verdict == "rejected" and any("frame" in r for r in reasons)
    assert gen.seen[0][0] == "video_auditor"


def test_a_whole_video_is_never_a_candidate():
    from app.documentary.visuals.director import rank_candidates

    src = VisualAsset(asset_code="S", asset_type="video_source", asset_role="context",
                      verification_status="source", local_path="x.mp4",
                      rights_status="public_domain", entity_key="the_lake")
    req = {"acceptable_roles": ["evidence", "context", "illustration"], "priority": "high"}
    assert rank_candidates(req, {"key": "the_lake", "name": "the lake"}, [src], set()) == []
