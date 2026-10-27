import json
from uuid import uuid4

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
