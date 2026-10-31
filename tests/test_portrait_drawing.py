"""The pencil drawing of a portrait: made once for a photo, the planner knows how many are needed, the owner's button makes them."""

from __future__ import annotations

import json
from uuid import uuid4

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.db.models import Case, EditorialBlueprint, StoryVersion, VisualAsset, VisualPlan
from app.documentary.drawing import portrait as PD
from app.documentary.drawing import service as S
from app.main import app


def photo_file(tmp_path):
    img = np.full((900, 700, 3), 200, np.uint8)
    cv2.ellipse(img, (350, 400), (200, 280), 0, 0, 360, (90, 90, 90), -1)             # a head
    cv2.circle(img, (270, 340), 28, (20, 20, 20), -1)
    cv2.circle(img, (430, 340), 28, (20, 20, 20), -1)
    cv2.ellipse(img, (350, 520), (90, 30), 0, 0, 180, (30, 30, 30), 8)
    path = tmp_path / "face.jpg"
    cv2.imwrite(str(path), img)
    return path


def test_a_photo_becomes_a_drawing_with_the_pencils_path_and_the_whole_drawing_takes_the_time_asked(tmp_path):
    drawing = PD.PortraitDrawing(photo_file(tmp_path))
    timeline = drawing.timeline(60.0)
    assert abs(timeline.seconds - 60.0) < 1e-6 and timeline.draws.any() and len(timeline.t) > 1000
    assert (np.diff(timeline.t) >= -1e-9).all(), "time runs forward"
    final = drawing.finished(timeline)
    assert final.shape == (PD.CH, PD.CW, 3) and (final < 150).any(), "graphite is on the paper"


def test_the_progress_video_and_the_pencils_position_for_every_frame_are_written(tmp_path):
    out = tmp_path / "drawing"
    seen = []
    record = PD.PortraitDrawing(photo_file(tmp_path)).render(out, seconds=6.0, fps=5, progress=seen.append)
    assert (out / "progress.mp4").stat().st_size > 1000 and record["frames"] == 31
    pen = np.load(out / "pen.npy")
    assert pen.shape == (31, 2) and pen[:, 0].max() > 100, "the pencil moves over the paper"
    cap = cv2.VideoCapture(str(out / "progress.mp4"))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) >= 30 and (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) == (PD.CW, PD.CH)
    first, last = cap.read()[1], None
    cap.set(cv2.CAP_PROP_POS_FRAMES, 29)
    last = cap.read()[1]
    assert last.mean() < first.mean() - 0.5, "the drawing grows: the last frame is darker than the first"


def _case(db, photos, record):
    case = Case(canonical_title="Portraits", slug=f"pd-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    if record is not None:
        db.add(VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json=json.dumps({"mvp": {"portraits": record}})))
    for code, priority in photos:
        db.add(VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", local_path=f"{code}.jpg", draw_priority=priority, spec_json="{}"))
    db.commit()
    return case


def test_the_planner_says_how_many_portraits_are_needed_and_which_photos_of_those_chosen(db_session):
    case = _case(db_session, [("A", 1), ("B", 2), ("C", 3)], {"needed": 2, "speed": 0.84, "order": [{"code": "A"}, {"code": "B"}], "uncovered": []})
    body = TestClient(app).get(f"/api/cases/{case.id}/drawing").json()
    assert (body["planned"], body["needed"], body["chosen"], body["ready"]) == (True, 2, 3, 0)
    assert [(r["code"], r["needed"]) for r in body["portraits"]] == [("A", True), ("B", True), ("C", False)]


def test_the_create_button_makes_only_the_needed_portraits_and_refuses_when_the_plan_or_the_gaps_are_not_ready(db_session, monkeypatch):
    started = []
    monkeypatch.setattr(S, "start", lambda asset: started.append(asset.asset_code) or True)
    client = TestClient(app)
    unplanned = _case(db_session, [("A", 1)], None)
    assert client.post(f"/api/cases/{unplanned.id}/drawing/create").status_code == 409
    blocked = _case(db_session, [("A", 1)], {"needed": 1, "speed": 1.0, "order": [{"code": "A"}], "uncovered": [{"start": 10, "end": 30}]})
    assert client.post(f"/api/cases/{blocked.id}/drawing/create").status_code == 409
    ok = _case(db_session, [("A", 1), ("B", 2), ("C", 3)], {"needed": 2, "speed": 0.9, "order": [{"code": "A"}, {"code": "B"}], "uncovered": []})
    assert client.post(f"/api/cases/{ok.id}/drawing/create").status_code == 200 and started == ["A", "B"]


def test_a_portrait_being_made_that_stopped_reporting_is_shown_as_interrupted_and_a_made_one_as_ready(db_session, tmp_path):
    asset = VisualAsset(case_id=1, asset_code="X", asset_type="photo", local_path="x.jpg")
    asset.spec_json = json.dumps({"drawing": {"status": "drawing", "progress": 0.4, "updated": 0}})
    assert S.state_of(asset)["status"] == "failed"
    asset.spec_json = json.dumps({"drawing": {"status": "drawing", "progress": 0.4, "updated": __import__("time").time()}})
    assert S.state_of(asset)["status"] == "drawing"


def _checked_case(db, ready, drawing=(), duration=1500.0, photos=3):
    record = {"needed": 3, "speed": 1.0, "order": [], "uncovered": [],
              "pool": [{"code": f"P{n}", "earned_from": 0.0, "refused": None} for n in range(1, photos + 1)],
              "estimate": {"duration": duration, "gaps": [], "card_seconds": 0.0}}
    case = _case(db, [(f"P{n}", n) for n in range(1, photos + 1)], record)
    for code in ready:
        asset = db.query(VisualAsset).filter_by(case_id=case.id, asset_code=code).one()
        video = f"/tmp/{uuid4().hex}.mp4"
        open(video, "w").close()
        asset.spec_json = json.dumps({"drawing": {"status": "ready", "video": video, "updated": 0}})
    for code in drawing:
        asset = db.query(VisualAsset).filter_by(case_id=case.id, asset_code=code).one()
        asset.spec_json = json.dumps({"drawing": {"status": "drawing", "updated": __import__("time").time()}})
    db.commit()
    return case


def test_when_the_made_portraits_are_not_enough_for_the_film_the_planner_orders_the_next_photo_in_priority_order(db_session, monkeypatch):
    ordered = []
    monkeypatch.setattr(S, "start", lambda asset: ordered.append(asset.asset_code) or True)
    case = _checked_case(db_session, ready=["P1"])
    result = S.check(db_session, case.id)
    assert (result["enough"], result["made"], result["ordered"]) == (False, 1, ["P2"]) and ordered == ["P2"]
    stored = json.loads(db_session.query(VisualPlan).filter_by(case_id=case.id).one().validation_json)["mvp"]["portraits"]["check"]
    assert stored["enough"] is False and stored["ordered"] == ["P2"]


def test_while_a_portrait_is_being_made_nothing_more_is_ordered_and_when_enough_are_made_the_check_passes(db_session, monkeypatch):
    ordered = []
    monkeypatch.setattr(S, "start", lambda asset: ordered.append(asset.asset_code) or True)
    waiting = _checked_case(db_session, ready=["P1"], drawing=["P2"])
    assert S.check(db_session, waiting.id)["ordered"] == [] and ordered == []
    enough = _checked_case(db_session, ready=["P1", "P2", "P3"])
    result = S.check(db_session, enough.id)
    assert result["enough"] is True and result["ordered"] == [] and 0.5 <= result["speed"] <= 2.0


def test_when_every_chosen_photo_is_made_and_still_not_enough_the_owner_is_to_choose_more(db_session, monkeypatch):
    monkeypatch.setattr(S, "start", lambda asset: True)
    case = _checked_case(db_session, ready=["P1", "P2"], photos=2, duration=3000.0)
    result = S.check(db_session, case.id)
    assert result["enough"] is False and result["exhausted"] is True and result["ordered"] == []


def test_a_film_the_made_portraits_cannot_fill_even_at_the_slowest_speed_does_not_fit():
    from app.documentary.drawing import plan as DP

    long_film = [{"kind": "image", "start": 0.0, "end": 6000.0}]
    assert DP.plan_drawing(long_film, [DP.Portrait("A", 1)]).fits is False
    assert DP.plan_drawing(long_film, [DP.Portrait(c, n) for n, c in enumerate("ABCDEFGHI", 1)]).fits is True
