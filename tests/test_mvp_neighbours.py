import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.models import Case, EditorialBlueprint, StoryVersion, VisualAsset, VisualPlan
from app.documentary.mvp import direct as D
from app.documentary.mvp import neighbours as NC
from app.documentary.visuals.usage import UsageTracker, asset_facts
from app.main import app

LENGTH = 40.0
WINDOW = {"start": 100.0, "end": 140.0}
SAID = "The detectives searched the house."


def _item(code, kind="photo", rank=5):
    asset = VisualAsset(case_id=1, asset_code=code, asset_type=kind, description=code, title=code, relevance_tier=2,
                        asset_role="context", duration_seconds=8.0 if kind == "video" else None,
                        clip_start=0.0 if kind == "video" else None, clip_end=8.0 if kind == "video" else None)
    return D.Item(asset, "context", None, rank, [], None)


def _photo(code, seconds):
    return {"command": "NEW_IMAGE", "asset_id": code, "share": seconds / LENGTH, "motion": "SLOW_PUSH", "speed": 1.0}


def _clip(code, seconds):
    return {"command": "SHOW_CLIP", "asset_id": code, "share": seconds / LENGTH, "motion": "NONE", "speed": 1.0}


def _gap(seconds):
    return {"command": "BLACK_SCREEN", "share": seconds / LENGTH, "gap": True, "said": SAID, "motion": "NONE",
            "speed": 1.0}


def cover(beats, items, windows=None, order=None, blocked=lambda bid, item: False, shown=()):
    items = {"B01": items} if isinstance(items, list) else items
    flat = [i for group in items.values() for i in group]
    tracker = UsageTracker({i.code: asset_facts(i.asset) for i in flat})
    for code, a, b in shown:
        tracker.add(code, a, b)
    for beat in beats:
        at = WINDOW["start"]
        for shot in beat["shots"]:
            if shot.get("asset_id"):
                tracker.add(shot["asset_id"], at, at + shot["share"] * LENGTH)
            at += shot["share"] * LENGTH
    window_of = windows or {b["beat_id"]: WINDOW for b in beats}
    run = NC.NeighbourCover(beats, window_of, items, tracker, {}, order or {"B01": 0, "B02": 1, "B03": 2},
                            blocked, D.related, D._shot)
    return run.run()


def seconds(shot):
    return round(shot["share"] * LENGTH, 2)


def test_two_photos_each_take_half_of_the_bare_moment():
    beats = [{"beat_id": "B01", "shots": [_photo("A", 10), _gap(10), _photo("B", 10), _photo("C", 10)]}]
    report = cover(beats, [_item("A"), _item("B"), _item("C")])
    shots = beats[0]["shots"]
    assert [(s["asset_id"], seconds(s)) for s in shots] == [("A", 15.0), ("B", 15.0), ("C", 10.0)]
    assert not any(s.get("gap") for s in shots) and report[0]["before"] == "held longer"
    assert round(sum(s["share"] for s in shots), 5) == 1.0


def test_a_still_is_never_held_longer_than_the_limit_and_a_photo_takes_the_rest():
    beats = [{"beat_id": "B01", "shots": [_photo("A", 18), _gap(12), _photo("B", 10)]}]
    cover(beats, [_item("A"), _item("B"), _item("P")])
    shots = beats[0]["shots"]
    assert max(seconds(s) for s in shots) <= 20.0 + 1e-6 and not any(s.get("gap") for s in shots)
    assert any(s.get("neighbour_cover") and s["asset_id"] == "P" for s in shots)


def test_a_video_neighbour_is_never_stretched_a_photo_covers_its_half():
    beats = [{"beat_id": "B01", "shots": [_clip("V", 10), _gap(10), _photo("B", 10), _photo("C", 10)]}]
    report = cover(beats, [_item("V", "video"), _item("B"), _item("C"), _item("P")])
    shots = beats[0]["shots"]
    assert seconds(shots[0]) == 10.0 and shots[0]["asset_id"] == "V"
    assert shots[1]["asset_id"] == "P" and seconds(shots[1]) == 5.0 and seconds(shots[2]) == 15.0
    assert report[0]["before"] == "a photo" and report[0]["after"] == "held longer"


def test_two_videos_get_photos_that_spoil_nothing():
    later = {"B01": [_item("EARLY"), _item("BLOCKED"), _item("SHOWN_LATER"), _item("V1", "video")],
             "B02": [_item("LATER_BEAT")]}
    beats = [{"beat_id": "B01", "shots": [_clip("V1", 10), _gap(10), _clip("V2", 10), _photo("X", 10)]}]
    cover(beats, later, blocked=lambda bid, item: item.code == "BLOCKED",
          shown=[("SHOWN_LATER", 130.0, 135.0)])
    picks = [s["asset_id"] for s in beats[0]["shots"] if s.get("neighbour_cover")]
    assert picks == ["EARLY"]
    assert "LATER_BEAT" not in picks and "BLOCKED" not in picks and "SHOWN_LATER" not in picks


def test_without_any_photo_the_moment_stays_bare_and_is_reported():
    beats = [{"beat_id": "B01", "shots": [_clip("V1", 10), _gap(10), _clip("V2", 20)]}]
    report = cover(beats, [_item("V1", "video"), _item("V2", "video")])
    assert any(s.get("gap") for s in beats[0]["shots"]) and report[0]["bare_seconds"] == 10.0


def test_a_neighbour_in_another_beat_is_continued_by_a_shot_of_its_own():
    beats = [{"beat_id": "B01", "shots": [_photo("A", 30), _photo("Z", 10)]},
             {"beat_id": "B02", "shots": [_gap(10), _photo("B", 30)]}]
    cover(beats, {"B01": [_item("A"), _item("Z"), _item("P")], "B02": [_item("B")]})
    first = beats[1]["shots"][0]
    assert first["asset_id"] == "Z" and first["continuation"] and seconds(first) == 5.0
    assert [s["asset_id"] for s in beats[1]["shots"]] == ["Z", "P", "B"] and seconds(beats[1]["shots"][1]) == 5.0


def _needs_plan(db):
    case = Case(canonical_title="Cover proof", slug=f"nc-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1,
                      validation_json=json.dumps({"mvp": {"reveal_graph_id": 1}}))
    db.add(plan)
    db.commit()
    return case, plan


def test_the_cover_is_off_until_the_owner_approves_it_and_the_choice_is_stored(db_session):
    case, plan = _needs_plan(db_session)
    assert D.cover_approved(plan) is False
    from app.db.base import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        answer = TestClient(app).post(f"/api/cases/{case.id}/mvp/neighbour-cover", json={"approved": True})
    finally:
        app.dependency_overrides.pop(get_db, None)
    db_session.refresh(plan)
    assert answer.status_code == 200 and answer.json()["approved"] is True and D.cover_approved(plan) is True
