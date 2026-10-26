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
