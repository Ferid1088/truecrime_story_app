"""Video pieces: a video is kept whole (muted) and cut by MEANING (the
segmenter) into named, described pieces; the Main Visual Planner judges each
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
        assert role == "mvp_segment_scout" and images
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
    monkeypatch.setattr("app.documentary.visuals.video_segmenter.get_generation_provider",
                        lambda: FakeSegmenterGen())
    r = client.post(f"/api/visuals/{source.id}/cut-again")
    assert r.status_code == 200, r.text
    assert r.json()["pieces"] == 4
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
# the planner's library
# ---------------------------------------------------------------------------


def test_a_whole_video_is_never_a_candidate(db_session):
    import uuid

    from app.db.models import Case, EditorialBlueprint, MvpRequirement, StoryVersion, VisualPlan
    from app.documentary.mvp.library import build_candidates

    case = Case(canonical_title="Lake", slug=uuid.uuid4().hex, language="en")
    db_session.add(case)
    db_session.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    db_session.add(plan)
    db_session.commit()
    db_session.add_all([
        MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                       function="orientation", entity_key="the_lake"),
        VisualAsset(case_id=case.id, asset_code="S", asset_type="video_source", asset_role="context",
                    verification_status="source", local_path="x.mp4", rights_status="public_domain",
                    entity_key="the_lake")])
    db_session.commit()
    out = build_candidates(db_session, plan, {"entities": [{"key": "the_lake", "name": "the lake",
                                                            "type": "place"}], "beats": []})
    assert out["created"] == 0 and out["requirements_without_candidates"] == 1
