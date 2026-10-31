import json
import subprocess
from uuid import uuid4

import pytest

from app.core.media_tools import ffmpeg_bin
from app.db.models import (Case, EditorialBlueprint, MvpCandidate, MvpRequirement, StoryVersion,
                           VisualAsset, VisualPlan)
from app.documentary.mvp import coverage as C


def _plan(db):
    case = Case(canonical_title="Coverage proof", slug=f"cv-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    db.add(plan)
    db.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", entity_key="e", role_needed="evidence", priority="high",
                         spec_json="{}")
    asset = VisualAsset(case_id=case.id, asset_code="V1", local_path="x.jpg")
    db.add_all([req, asset])
    db.commit()
    db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, role="evidence",
                        final_status="approved"))
    db.commit()
    return plan


def shot(a, b, kind="image", asset="V1", beat="B01"):
    return {"start": a, "end": b, "kind": kind, "asset_id": asset if kind != "black" else None,
            "beat_id": beat}


def test_full_coverage_with_an_approved_asset_is_review_ready(db_session):
    plan = _plan(db_session)
    script = {"duration": 8.0, "shots": [shot(0, 4), shot(4, 8, asset="V1")]}
    report = C.measure(script, plan, db_session, [{"start": 0, "end": 7, "display": "x"}])
    assert report["temporal_coverage"] == 1.0 and report["requirements"]["covered"] == 1
    assert C.verdict(report)["review_ready"] is True


def test_black_stretch_unapproved_asset_and_long_still_block_review(db_session):
    plan = _plan(db_session)
    script = {"duration": 40.0, "shots": [shot(0, 30, asset="V9"), shot(30, 40, kind="black")]}
    report = C.measure(script, plan, db_session, [{"start": 31, "end": 39, "display": "uncovered line"}])
    verdict = C.verdict(report)
    assert not verdict["review_ready"]
    joined = " ".join(verdict["blocking"])
    assert "black stretch" in joined and "without an approved verdict" in joined and "held beyond" in joined
    assert report["weak_sentence_count"] == 1 and report["requirements"]["uncovered"] == 1


def test_shortage_requirements_block_publication_only(db_session):
    plan = _plan(db_session)
    req = db_session.query(MvpRequirement).filter_by(visual_plan_id=plan.id).one()
    req.status = "shortage"
    db_session.query(MvpCandidate).filter_by(requirement_id=req.id).delete()
    db_session.commit()
    script = {"duration": 4.0, "shots": [shot(0, 4, kind="map", asset="MAP1")]}
    verdict = C.verdict(C.measure(script, plan, db_session, []))
    assert verdict["review_ready"] and verdict["publication_blockers"]


@pytest.mark.skipif(ffmpeg_bin() is None, reason="ffmpeg missing")
def test_probe_reads_the_real_file(tmp_path):
    out = tmp_path / "t.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True, capture_output=True)
    info = C.probe_file(out)
    assert (info["width"], info["height"], info["video_codec"]) == (320, 180, "h264")
    assert 1.8 < info["duration"] < 2.3 and info["black_seconds"] == 0.0


def test_a_gap_the_pencil_drawing_fills_the_whole_screen_of_is_covered_and_not_black(db_session):
    plan = _plan(db_session)
    script = {"duration": 40.0, "shots": [shot(0, 20), shot(20, 40, kind="black")],
              "drawing": {"segments": [{"start": 20.0, "end": 40.0, "mode": "full"}]}}
    report = C.measure(script, plan, db_session, [])
    assert report["temporal_coverage"] == 1.0 and report["black_seconds"] == 0.0 and report["longest_black"] == 0.0
    undrawn = C.measure({"duration": 40.0, "shots": script["shots"]}, plan, db_session, [])
    assert undrawn["temporal_coverage"] == 0.5 and undrawn["longest_black"] == 20.0


def test_a_picture_approved_in_an_earlier_round_is_not_flagged_and_a_picture_may_stay_27_seconds(db_session):
    plan = _plan(db_session)
    earlier = VisualPlan(case_id=plan.case_id, blueprint_id=plan.blueprint_id, version=0)
    db_session.add(earlier)
    db_session.commit()
    req = MvpRequirement(case_id=plan.case_id, visual_plan_id=earlier.id, requirement_key="OLD.1", beat_id="B09",
                         function="evidence", entity_key="e", role_needed="evidence", priority="high", spec_json="{}")
    asset = VisualAsset(case_id=plan.case_id, asset_code="OLDV", local_path="o.jpg")
    db_session.add_all([req, asset])
    db_session.commit()
    db_session.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, role="evidence", final_status="approved"))
    db_session.commit()
    script = {"duration": 27.0, "shots": [shot(0, 27, asset="OLDV")]}
    report = C.measure(script, plan, db_session, [])
    assert report["unapproved_assets"] == [] and report["long_stills"] == []


def test_a_picture_held_longer_to_cover_a_gap_is_not_a_picture_held_too_long(db_session):
    plan = _plan(db_session)
    covered = {**shot(0, 33), "gap_cover_seconds": 6.0}                      # 27 s of its own + 6 s of a gap its neighbour could not
    assert C.measure({"duration": 33.0, "shots": [covered]}, plan, db_session, [])["long_stills"] == []
    assert len(C.measure({"duration": 33.0, "shots": [shot(0, 33)]}, plan, db_session, [])["long_stills"]) == 1
