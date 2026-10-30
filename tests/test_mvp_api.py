import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models import (Case, EditorialBlueprint, MvpCandidate, MvpRequirement, StoryVersion,
                           VisualAsset, VisualPlan)
from app.main import app


def _seed(db, rights="public_domain"):
    case = Case(canonical_title="API proof", slug=f"api-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1,
                      validation_json=json.dumps({"mvp": {"reveal_graph_id": 7}}))
    db.add(plan)
    db.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", entity_key="caleb", role_needed="evidence", priority="high",
                         spec_json=json.dumps({"need": "his face"}))
    asset = VisualAsset(case_id=case.id, asset_code=f"V{uuid4().hex[:6]}", local_path="x.jpg",
                        rights_status=rights)
    db.add_all([req, asset])
    db.commit()
    cand = MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, identity_status="needs_review",
                        provenance_status="passed", ethics_status="approved", scene_status="approved",
                        rights_status="cleared" if rights == "public_domain" else "blocked",
                        final_status="needs_review", role="evidence")
    db.add(cand)
    db.commit()
    return case, plan, cand


def test_the_plan_view_lists_requirements_verdict_totals_and_shortages(db_session):
    case, plan, cand = _seed(db_session)
    body = TestClient(app).get(f"/api/cases/{case.id}/mvp/plan").json()
    assert body["reveal_graph_id"] == 7 and body["candidate_totals"]["needs_review"] == 1
    assert body["requirements"][0]["key"] == "B01.1" and body["shortages"] == 0


def test_candidates_waiting_for_review_are_listed_with_their_five_decisions(db_session):
    case, plan, cand = _seed(db_session)
    [row] = TestClient(app).get(f"/api/cases/{case.id}/mvp/candidates").json()
    assert row["identity"] == "needs_review" and row["need"] == "his face" and row["thumb_url"]


def test_the_owner_can_approve_or_reject_and_the_decision_is_recorded(db_session):
    case, plan, cand = _seed(db_session)
    client = TestClient(app)
    out = client.post(f"/api/mvp/candidates/{cand.id}/decision", json={"decision": "approve", "note": "ok"}).json()
    assert out["status"] == "approved" and out["verifiers"][-1]["verifier"] == "owner"
    out = client.post(f"/api/mvp/candidates/{cand.id}/decision", json={"decision": "reject"}).json()
    assert out["status"] == "rejected"
    assert client.post(f"/api/mvp/candidates/{cand.id}/decision", json={"decision": "maybe"}).status_code == 422


def test_blocked_rights_cannot_be_overridden_by_a_picture_approval(db_session):
    case, plan, cand = _seed(db_session, rights="do_not_use")
    r = TestClient(app).post(f"/api/mvp/candidates/{cand.id}/decision", json={"decision": "approve"})
    assert r.status_code == 409


def test_each_picture_is_listed_once_and_one_decision_settles_all_its_requirements(db_session):
    case, plan, cand = _seed(db_session)
    others = []
    for key in ("B02.1", "B03.1"):                       # the same picture judged again for other requirements
        req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key=key, beat_id=key[:3],
                             function="evidence", entity_key="caleb", role_needed="evidence", priority="high",
                             spec_json="{}")
        db_session.add(req)
        db_session.commit()
        others.append(MvpCandidate(requirement_id=req.id, visual_asset_id=cand.visual_asset_id,
                                   identity_status="needs_review", provenance_status="passed", ethics_status="approved",
                                   scene_status="approved", rights_status="cleared", final_status="needs_review",
                                   role="evidence"))
    db_session.add_all(others)
    db_session.commit()
    client = TestClient(app)
    plain = client.get(f"/api/cases/{case.id}/mvp/candidates?status=needs_review").json()
    listed = client.get(f"/api/cases/{case.id}/mvp/candidates?status=needs_review&group=true").json()
    assert len(plain) == 3 and len(listed) == 1
    assert sorted(listed[0]["candidate_ids"]) == sorted([cand.id] + [o.id for o in others])
    assert [r["beat_id"] for r in listed[0]["requirements"]] == ["B01", "B02", "B03"]
    done = client.post(f"/api/cases/{case.id}/mvp/candidates/decision",
                       json={"candidate_ids": listed[0]["candidate_ids"], "decision": "approve"}).json()
    assert done == {"decided": 3, "skipped": [], "decision": "approve"}
    assert client.get(f"/api/cases/{case.id}/mvp/candidates?status=needs_review&group=true").json() == []
    assert len(client.get(f"/api/cases/{case.id}/mvp/candidates?status=approved&group=true").json()) == 1
    again = client.post(f"/api/cases/{case.id}/mvp/candidates/decision",
                        json={"candidate_ids": listed[0]["candidate_ids"], "decision": "reject"}).json()
    assert again["decided"] == 3                          # a decided picture can be decided the other way
    assert len(client.get(f"/api/cases/{case.id}/mvp/candidates?status=rejected&group=true").json()) == 1


def test_a_picture_the_rights_block_is_not_approved_by_a_group_decision(db_session):
    case, plan, cand = _seed(db_session, rights="all_rights_reserved")
    client = TestClient(app)
    answer = client.post(f"/api/cases/{case.id}/mvp/candidates/decision",
                         json={"candidate_ids": [cand.id], "decision": "approve"})
    assert answer.status_code == 409
    other = client.post(f"/api/cases/{case.id + 999}/mvp/candidates/decision",
                        json={"candidate_ids": [cand.id], "decision": "reject"})
    assert other.status_code == 404                       # never another case's candidates


def test_the_owner_can_approve_a_rights_blocked_picture_on_their_own_decision(db_session):
    case, plan, cand = _seed(db_session, rights="all_rights_reserved")
    cand.rights_status = "blocked"
    db_session.commit()
    client = TestClient(app)
    answer = client.post(f"/api/cases/{case.id}/mvp/candidates/decision",
                         json={"candidate_ids": [cand.id], "decision": "approve", "override_rights": True})
    assert answer.status_code == 200
    db_session.expire_all()
    asset = db_session.get(VisualAsset, cand.visual_asset_id)
    assert asset.rights_status == "editorial_review_required"
    assert asset.rights_reason.startswith("owner override of all_rights_reserved")
    assert asset.editorial_approved_at is not None
    assert db_session.get(MvpCandidate, cand.id).rights_status == "preview_only"   # usable in the review film only
    assert db_session.get(MvpCandidate, cand.id).final_status == "approved"
    listed = client.get(f"/api/cases/{case.id}/mvp/candidates?status=all&group=true").json()
    assert len(listed) == 1


def test_a_listed_video_carries_the_window_the_planner_would_use(db_session):
    case, plan, cand = _seed(db_session)
    asset = db_session.get(VisualAsset, cand.visual_asset_id)
    asset.asset_type, asset.clip_start, asset.clip_end, asset.duration_seconds = "video", 12.5, 30.0, 418.4
    db_session.commit()
    listed = TestClient(app).get(f"/api/cases/{case.id}/mvp/candidates?status=all&group=true").json()
    assert (listed[0]["asset_type"], listed[0]["clip_start"], listed[0]["clip_end"]) == ("video", 12.5, 30.0)


def test_the_owner_note_is_stored_listed_and_limited(db_session):
    case, plan, cand = _seed(db_session)
    client = TestClient(app)
    asset = db_session.get(VisualAsset, cand.visual_asset_id)
    asset.spec_json = json.dumps({"timing": {"phase": "trial"}, "keep": 1})
    db_session.commit()
    url = f"/api/cases/{case.id}/mvp/assets/{asset.id}/note"
    out = client.put(url, json={"note": "  Ashley's  sister,\nKayla Keyt "}).json()
    assert out == {"asset_id": asset.id, "owner_note": "Ashley's sister, Kayla Keyt"}
    [row] = client.get(f"/api/cases/{case.id}/mvp/candidates").json()
    assert row["owner_note"] == "Ashley's sister, Kayla Keyt"
    db_session.refresh(asset)
    assert json.loads(asset.spec_json) == {"keep": 1}                   # judged again for its timing, with the note
    assert client.put(url, json={"note": "x" * 401}).status_code == 422
    assert client.put(url, json={"note": "  "}).json()["owner_note"] is None
    assert client.put(f"/api/cases/{case.id + 999}/mvp/assets/{asset.id}/note", json={"note": "a"}).status_code == 404


def test_the_run_always_stops_once_for_the_owners_review_of_every_picture_and_goes_on_after_it(db_session):
    from app.documentary.mvp.pipeline import request_review, stage_owner_attention
    from app.documentary.pipeline.errors import AwaitingPictureReview

    case, plan, cand = _seed(db_session)
    with pytest.raises(AwaitingPictureReview, match="every photo and video"):
        stage_owner_attention(db_session, plan, "en")
    response = TestClient(app).post(f"/api/cases/{case.id}/mvp/candidates/decision",
                                    json={"candidate_ids": [cand.id], "decision": "reject"})
    assert response.status_code == 200
    request_review(db_session, plan)           # what the language chain does when the gate stops the run
    db_session.expire_all()
    assert stage_owner_attention(db_session, plan, "en") is None, "it is shown once: the run goes on after Resume"
    assert db_session.get(type(cand), cand.id).final_status == "rejected"


def test_the_owner_marks_what_must_be_used_and_gives_photos_a_draw_priority(db_session):
    from app.db.models import VisualAsset

    case, plan, cand = _seed(db_session)
    asset = db_session.get(VisualAsset, cand.visual_asset_id)
    client, url = TestClient(app), f"/api/cases/{case.id}/mvp/assets/{asset.id}/selection"
    assert client.put(url, json={"must_use": True, "draw_priority": 1}).json() == {
        "asset_id": asset.id, "must_use": True, "draw_priority": 1}
    other = VisualAsset(case_id=case.id, asset_code="PRIO2", local_path="y.jpg", asset_type="photo")
    clip = VisualAsset(case_id=case.id, asset_code="CLIP1", local_path="z.mp4", asset_type="video")
    db_session.add_all([other, clip])
    db_session.commit()
    assert client.put(f"/api/cases/{case.id}/mvp/assets/{other.id}/selection", json={"draw_priority": 1}).status_code == 409
    assert client.put(f"/api/cases/{case.id}/mvp/assets/{clip.id}/selection", json={"draw_priority": 2}).status_code == 422
    assert client.put(url, json={"draw_priority": 11}).status_code == 422
    assert client.put(f"/api/cases/{case.id}/mvp/assets/{clip.id}/selection", json={"must_use": True}).json()["must_use"] is True
    assert client.put(f"/api/cases/{case.id + 999}/mvp/assets/{asset.id}/selection", json={}).status_code == 404


def test_the_review_of_the_trimmed_and_cut_pictures_shows_only_those_and_the_run_stops_once(db_session):
    from app.db.models import VisualAsset
    from app.documentary.mvp import pipeline as PL

    case, plan, cand = _seed(db_session)
    cand.final_status = "approved"
    asset = db_session.get(VisualAsset, cand.visual_asset_id)
    asset.spec_json = json.dumps({"overlays": {"status": "cut", "marks": [{"kind": "logo", "what": "a channel logo"}],
                                                "crop": [0.0, 0.0, 0.9, 1.0]}})
    db_session.commit()
    assert [c.id for c in PL.cut_items(db_session, plan)] == [cand.id]
    asset.spec_json = "{}"
    db_session.commit()
    assert PL.cut_items(db_session, plan) == [], "an untouched picture is not shown again in review 2"


def test_the_candidate_list_can_be_limited_to_the_trimmed_and_cut_pictures(db_session):
    from app.db.models import VisualAsset

    case, _, cand = _seed(db_session)
    cand.final_status = "approved"
    asset = db_session.get(VisualAsset, cand.visual_asset_id)
    db_session.commit()
    url = f"/api/cases/{case.id}/mvp/candidates?status=all&group=true&cut_only=true"
    client = TestClient(app)
    assert client.get(url).json() == []
    asset.spec_json = json.dumps({"overlays": {"status": "cut", "marks": [{"kind": "logo", "what": "a channel logo"}],
                                                "crop": [0.0, 0.0, 0.9, 1.0]}})
    db_session.commit()
    assert [g["asset_id"] for g in client.get(url).json()] == [asset.id]


def test_a_picture_the_owner_checked_is_put_into_the_film_when_the_direction_left_it_out(db_session):
    from app.db.models import VisualAsset, VisualPlan
    from app.documentary.mvp.must_use import place_must_use

    case, plan, cand = _seed(db_session)
    cand.final_status = "approved"
    asset = db_session.get(VisualAsset, cand.visual_asset_id)
    asset.must_use = True
    target = VisualPlan(case_id=case.id, blueprint_id=plan.blueprint_id, version=9, status="planned", plan_json=json.dumps({
        "beats": [{"beat_id": cand.requirement.beat_id if hasattr(cand, "requirement") else "B01", "shots": [
            {"command": "NEW_IMAGE", "asset_id": "OTHER", "share": 0.3}, {"command": "BLACK_SCREEN", "gap": True, "share": 0.2}]}]}))
    db_session.add(target)
    db_session.commit()
    beat = (db_session.query(MvpRequirement.beat_id).filter_by(id=cand.requirement_id).scalar())
    target.plan_json = json.dumps({"beats": [{"beat_id": beat, "shots": [
        {"command": "NEW_IMAGE", "asset_id": "OTHER", "share": 0.3}, {"command": "BLACK_SCREEN", "gap": True, "share": 0.2}]}]})
    db_session.commit()
    report = place_must_use(db_session, case.id, plan, target)
    shots = json.loads(target.plan_json)["beats"][0]["shots"]
    assert report["placed"] == [{"asset": asset.asset_code, "beat": beat}] and shots[1]["asset_id"] == asset.asset_code
    assert shots[1]["command"] == "NEW_IMAGE" and shots[1].get("gap") is None and shots[0]["asset_id"] == "OTHER"
    assert place_must_use(db_session, case.id, plan, target)["already"] == [asset.asset_code], "used once"
    cand.final_status = "rejected"
    asset.asset_code = "OTHER2"
    other = VisualAsset(case_id=case.id, asset_code="NOTAPPROVED", local_path="n.jpg", asset_type="photo", must_use=True)
    db_session.add(other)
    db_session.commit()
    failed = {f["asset"] for f in place_must_use(db_session, case.id, plan, target)["failed"]}
    assert failed == {"OTHER2", "NOTAPPROVED"}, "a checked item that is not approved cannot be shown, and is reported"


def test_a_new_run_stops_for_review_1_even_when_an_earlier_run_was_reviewed(db_session):
    from app.documentary.mvp.pipeline import request_review, restart_reviews, stage_owner_attention
    from app.documentary.pipeline.errors import AwaitingPictureReview

    _, plan, _ = _seed(db_session)
    request_review(db_session, plan)
    request_review(db_session, plan)
    assert stage_owner_attention(db_session, plan, "en") is None
    restart_reviews(db_session, plan)
    with pytest.raises(AwaitingPictureReview, match="every photo and video"):
        stage_owner_attention(db_session, plan, "en")
