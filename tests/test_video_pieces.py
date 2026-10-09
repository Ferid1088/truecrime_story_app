"""Video pieces: a video is kept whole (muted) and cut by MEANING (the
segmenter) into named, described pieces; the video auditor checks each
cut, name, description and every frame (corrections are checked again);
a sentence shows only its piece, overlapping pieces never both appear."""
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


class FakeSegmenterGen:
    """Cuts the three-scene video by meaning: one piece per scene, the long
    blue scene in two overlapping pieces."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    async def generate_structured(self, role, system, user, images=None):
        assert role == "video_segmenter" and images
        data = json.loads(user)
        self.calls.append(data)
        if self.fail:
            raise RuntimeError("model down")
        assert "Cut by MEANING" in system and len(images) == len(data["frame_times"])
        a, b = data["stretch"]
        pieces = [p for p in [
            {"start": 0.0, "end": 6.0, "name": "Red screen", "description": "A plain red frame."},
            {"start": 6.0, "end": 12.0, "name": "Colour bars", "description": "A test pattern."},
            {"start": 12.0, "end": 30.0, "name": "Blue sky, first part",
             "description": "Plain blue."},
            {"start": 28.0, "end": 42.0, "name": "Blue sky, second part",
             "description": "Plain blue."},
        ] if a <= p["start"] < b]
        return {"pieces": pieces}, GenerationResult(text="{}", model="m/seg", provider="fake")


def _ingest(db, case, path, meta, gen=None):
    from app.documentary.visuals.video_segmenter import VideoSegmenter

    return asyncio.run(PC.ingest_video(db, case, path, meta,
                                       segmenter=VideoSegmenter(gen=gen or FakeSegmenterGen())))


# ---------------------------------------------------------------------------
# cutting
# ---------------------------------------------------------------------------


def test_segmenter_proposals_are_checked_before_they_become_pieces():
    from app.documentary.visuals.video_segmenter import check_proposals, windows

    cuts = [6.0, 12.0, 30.4]
    got = check_proposals([
        {"start": 0.1, "end": 5.8, "name": "Red frame", "description": "A red frame."},
        {"start": 6.2, "end": 12.3, "name": "Test pattern", "description": "Bars."},
        {"start": 6.0, "end": 12.0, "name": "dup", "description": "same cut again"},
        {"start": 12.0, "end": 14.0, "name": "Too short", "description": "x"},
        {"start": 12.0, "end": 41.0, "name": "Too long", "description": "x"},
        {"start": 14.0, "end": 30.0, "name": "", "description": "no name"},
        {"start": 14.0, "end": 30.0, "name": "Blue", "description": "Blue sky."},
    ], 0, 42, cuts, 42.0)
    # cut points snap to the scene change near them; no duplicates, no
    # pieces without a name/description or outside the length limits
    assert [(p["start"], p["end"], p["name"]) for p in got] == [
        (0.0, 6.0, "Red frame"), (6.0, 12.0, "Test pattern"), (14.0, 30.4, "Blue")]
    # stretches for the segmenter end at a scene change when one is near
    assert windows(200.0, [70.0, 85.0, 150.0], 90.0) == [(0.0, 85.0), (85.0, 150.0),
                                                        (150.0, 200.0)]
    assert windows(200.0, [], 90.0) == [(0.0, 90.0), (90.0, 180.0), (180.0, 200.0)]


@needs_ffmpeg
def test_scene_changes_are_detected(env):
    v = _three_scenes(env / "src.mp4")
    cuts = PC.detect_cuts(v)
    assert any(abs(c - 6) < 0.3 for c in cuts) and any(abs(c - 12) < 0.3 for c in cuts)


@needs_ffmpeg
def test_a_video_is_kept_whole_and_cut_into_described_pieces(db_session, env):
    case = _case(db_session)
    v = _three_scenes(env / "src.mp4")
    gen = FakeSegmenterGen()
    source, pieces = _ingest(db_session, case, v, {
        "provider": "upload", "rights_status": "owned", "asset_role": "evidence",
        "title": "Search at the lake", "entities": ["the_lake"], "entity_key": "the_lake",
        "found_during": "upload"}, gen)
    # the segmenter saw the scene changes it can cut at
    assert any(abs(c - 6) < 0.3 for c in gen.calls[0]["scene_changes"])
    assert source.asset_type == "video_source" and source.verification_status == "source"
    from app.documentary.visuals.footage import probe_video, sheet_path

    info = probe_video(storage.resolve(source.local_path))
    assert info["has_video"] and not info["has_audio"] and info["duration"] == pytest.approx(42, abs=0.2)
    # cut where the meaning changes, named and described by the segmenter
    assert [p.title for p in pieces] == ["Red screen", "Colour bars", "Blue sky, first part",
                                         "Blue sky, second part"]
    assert pieces[1].description == "A test pattern."
    assert (pieces[2].clip_start, pieces[2].clip_end) == (12.0, 30.0)
    assert pieces[3].clip_start < pieces[2].clip_end            # a meaningful overlap
    assert json.loads(source.spec_json)["pieces"] == [p.asset_code for p in pieces]
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


@needs_ffmpeg
def test_a_video_the_segmenter_cannot_cut_is_kept_but_never_cut_by_the_clock(db_session, env):
    case = _case(db_session)
    source, pieces = _ingest(db_session, case, _three_scenes(env / "f.mp4"), {
        "provider": "upload", "rights_status": "owned", "title": "x",
        "found_during": "upload"}, FakeSegmenterGen(fail=True))
    assert pieces == [] and source.asset_type == "video_source"
    assert "model down" in json.loads(source.spec_json)["segment_error"]
    assert db_session.query(VisualAsset).filter_by(case_id=case.id,
                                                   asset_type="video").count() == 0
    # "Cut again" (or the next research run) cuts it by meaning once the
    # segmenter works; a video that already has pieces is never cut twice
    from app.documentary.visuals.video_segmenter import VideoSegmenter

    gen = FakeSegmenterGen()
    pieces = asyncio.run(PC.cut_video(db_session, case, source, VideoSegmenter(gen=gen)))
    assert [p.title for p in pieces][:2] == ["Red screen", "Colour bars"]
    assert "segment_error" not in json.loads(source.spec_json)
    calls = len(gen.calls)
    again = asyncio.run(PC.cut_video(db_session, case, source, VideoSegmenter(gen=gen)))
    assert [p.id for p in again] == [p.id for p in pieces] and len(gen.calls) == calls


@needs_ffmpeg
def test_cut_again_endpoint(client, db_session, env, monkeypatch):
    monkeypatch.setattr("app.documentary.visuals.video_segmenter.get_generation_provider",
                        lambda: FakeSegmenterGen(fail=True))
    case = _case(db_session)
    source, _ = _ingest(db_session, case, _three_scenes(env / "g.mp4"), {
        "provider": "upload", "rights_status": "owned", "title": "x",
        "found_during": "upload"}, FakeSegmenterGen(fail=True))
    r = client.post(f"/api/visuals/{source.id}/cut-again")
    assert r.status_code == 502 and "model down" in r.json()["detail"]
    checked = []
    monkeypatch.setattr("app.documentary.api._verify_later",
                        lambda case_id, asset_id: checked.append(asset_id))
    monkeypatch.setattr("app.documentary.visuals.video_segmenter.get_generation_provider",
                        lambda: FakeSegmenterGen())
    r = client.post(f"/api/visuals/{source.id}/cut-again")
    assert r.status_code == 200, r.text
    assert r.json()["pieces"] == 4 and checked == [source.id]   # pieces go to the auditor
    photo = VisualAsset(case_id=case.id, asset_code=f"PH{uuid.uuid4().hex[:6]}",
                        asset_type="photo")
    db_session.add(photo)
    db_session.commit()
    assert client.post(f"/api/visuals/{photo.id}/cut-again").status_code == 400


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


class SeqGen:
    """Answers the video auditor with the given answers in turn."""

    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    async def generate_structured(self, role, system, user, images=None):
        self.seen.append((role, json.loads(user), len(images or [])))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return dict(a), GenerationResult(text="{}", model="m/video", provider="fake")


OK = {"cut_ok": True, "description_ok": True, "name": "Colour bars",
      "description": "A test pattern.", "subject_type": "other", "matches_claim": "yes",
      "role": "context", "entities": ["the_lake", "unknown"], "period_ok": "yes",
      "tone_ok": True, "text_or_logo_in_any_frame": False,
      "graphic_or_sensitive_in_any_frame": False, "quality": 0.8, "confidence": 0.9}


def _check(db, case, piece, gen):
    from app.documentary.visuals.video_auditor import VideoAuditor

    asyncio.run(VideoAuditor(gen=gen).describe(
        db, case, piece, [{"key": "the_lake", "name": "the lake", "type": "place"}], []))


@needs_ffmpeg
def test_the_video_auditor_checks_the_cut_the_description_and_every_frame(db_session, env):
    case = _case(db_session)
    _, pieces = _ingest(db_session, case, _three_scenes(env / "v.mp4"), {
        "provider": "internet_archive", "rights_status": "public_domain",
        "asset_role": "context", "title": "lake film", "found_during": "research"})
    red, bars, blue1, blue2 = pieces
    # all right: the segmenter's name and description stay
    gen = SeqGen(OK)
    _check(db_session, case, bars, gen)
    role, payload, n = gen.seen[0]
    assert role == "video_auditor" and payload["proposed"]["name"] == "Colour bars"
    assert payload["frames"][0] == "context before" and payload["frames"][-1] == "context after"
    assert n == len(payload["frames"]) >= 7
    assert bars.verification_status == "verified" and bars.title == "Colour bars"
    assert "the_lake" in bars.entities_json and "unknown" not in bars.entities_json

    # a wrong cut is moved to the auditor's better start/end and checked again
    gen = SeqGen({**OK, "cut_ok": False, "suggested_start": 13.0, "suggested_end": 29.0,
                  "reasons": ["starts on the last frame of the bars"]}, OK)
    _check(db_session, case, blue1, gen)
    assert len(gen.seen) == 2 and (blue1.clip_start, blue1.clip_end) == (13.0, 29.0)
    assert json.loads(blue1.spec_json)["window"] == [13.0, 29.0]
    assert blue1.verification_status == "verified"
    checks = json.loads(blue1.verification_json)["checks"]
    assert checks[0]["cut_ok"] is False and checks[1]["cut_ok"] is True

    # a wrong description is replaced by what the auditor sees, checked again
    gen = SeqGen({**OK, "description_ok": False, "name": "Plain blue screen",
                  "description": "A plain blue frame, no sky visible."}, OK)
    _check(db_session, case, blue2, gen)
    assert blue2.title == "Plain blue screen" and "no sky" in blue2.description
    assert blue2.verification_status == "verified" and len(gen.seen) == 2

    # still not meaningful after the redos (or no usable fix): rejected
    gen = SeqGen({**OK, "cut_ok": False, "suggested_start": None, "suggested_end": None})
    _check(db_session, case, red, gen)
    assert red.verification_status == "rejected"
    assert json.loads(red.verification_json)["reason"] == "cut_not_meaningful"


@needs_ffmpeg
def test_one_bad_frame_fails_the_piece(db_session, env):
    case = _case(db_session)
    _, pieces = _ingest(db_session, case, _three_scenes(env / "u.mp4"), {
        "provider": "internet_archive", "rights_status": "public_domain",
        "asset_role": "context", "title": "lake film", "found_during": "research"})
    _check(db_session, case, pieces[0], SeqGen({**OK, "text_or_logo_in_any_frame": True}))
    assert pieces[0].verification_status == "rejected"
    _check(db_session, case, pieces[1], SeqGen({**OK, "graphic_or_sensitive_in_any_frame": True}))
    assert pieces[1].verification_status == "rejected"


@needs_ffmpeg
def test_placement_fails_when_a_frame_fails(db_session, env):
    from app.documentary.visuals.auditor import VisualAuditor

    case = _case(db_session)
    _, pieces = _ingest(db_session, case, _three_scenes(env / "w.mp4"), {
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
