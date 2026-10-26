import json
from uuid import uuid4

from app.db.models import (Case, EditorialBlueprint, MvpCandidate, MvpRequirement, ProductionScript,
                           StoryVersion, VisualAsset, VisualAudit, VisualPlan)
from app.documentary.mvp import library as L

ENTITY = {"key": "caleb_flynn", "type": "person", "name": "Caleb Flynn", "aliases": ["Caleb"]}


def _case(db):
    case = Case(canonical_title="Library proof", slug=f"lib-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    return case, bp, sv


def _asset(db, case, **kw):
    a = VisualAsset(case_id=case.id, asset_code=f"V{uuid4().hex[:6]}", local_path="x.jpg", **kw)
    db.add(a)
    db.commit()
    return a


def test_relevance_prefers_entity_key_then_search_then_text(db_session):
    case, _bp, _sv = _case(db_session)
    keyed = _asset(db_session, case, entity_key="caleb_flynn")
    searched = _asset(db_session, case, found_for="Caleb Flynn murder trial")
    other = _asset(db_session, case, found_for="Troy Ohio street")
    assert L.relevance(keyed, ENTITY) >= 3.0
    assert 1.0 <= L.relevance(searched, ENTITY) < 3.0
    assert L.relevance(other, ENTITY) == 0.0


def test_build_candidates_is_idempotent_and_capped(db_session):
    case, bp, _sv = _case(db_session)
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    db_session.add(plan)
    db_session.commit()
    db_session.add(MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1",
                                  beat_id="B01", function="evidence", entity_key="caleb_flynn"))
    for _ in range(20):
        _asset(db_session, case, entity_key="caleb_flynn")
    db_session.commit()
    reqs = {"entities": [ENTITY], "beats": []}
    first = L.build_candidates(db_session, plan, reqs)
    second = L.build_candidates(db_session, plan, reqs)
    assert first["created"] == 12 and second["created"] == 0 and second["reused"] == 12
    requirement = db_session.query(MvpRequirement).filter_by(visual_plan_id=plan.id).one()
    assert db_session.query(MvpCandidate).filter_by(requirement_id=requirement.id).count() == 12


def test_reset_clears_old_decisions_but_keeps_files_and_mvp_plans(db_session):
    case, bp, sv = _case(db_session)
    asset = _asset(db_session, case, asset_type="photo", verification_status="rejected",
                   verification_json=json.dumps({"old": True}), sha256="a" * 64)
    old = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    mine = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=2,
                      validation_json=json.dumps({"mvp": {}}))
    db_session.add_all([old, mine, VisualAudit(case_id=case.id, production_script_id=None) if False else old])
    db_session.add(ProductionScript(case_id=case.id, story_version_id=sv.id, language="en", version=1,
                                    script_json="{}"))
    db_session.commit()
    removed = L.reset_case_visual_state(db_session, case.id)
    db_session.refresh(asset)
    assert asset.verification_status == "unverified" and asset.verification_json is None
    assert asset.sha256 == "a" * 64 and asset.local_path == "x.jpg"
    assert removed["production_scripts"] == 1
    assert [p.version for p in db_session.query(VisualPlan).filter_by(case_id=case.id)] == [2]


def test_a_languages_shot_list_row_is_never_taken_for_the_shared_needs_plan(db_session):
    from app.documentary.mvp.needs import latest_mvp_plan

    case, bp, _sv = _case(db_session)
    needs = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1,
                       validation_json=json.dumps({"mvp": {"reveal_graph_id": 1}, "requirements": {}}))
    shots = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=2,
                       validation_json=json.dumps({"mvp": {"language": "en", "needs_plan_id": 1}}))
    db_session.add_all([needs, shots])
    db_session.commit()
    assert latest_mvp_plan(db_session, bp.id).id == needs.id


def test_the_top_up_attaches_unjudged_pictures_to_place_requirements_of_bare_beats_only(db_session):
    case, bp, _sv = _case(db_session)
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    db_session.add(plan)
    db_session.commit()
    court = {"key": "county_court", "type": "building", "name": "Miami County Courthouse"}
    judge = {"key": "judge_pratt", "type": "person", "name": "Judge Pratt"}
    db_session.add_all([
        MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                       function="orientation", entity_key="county_court", role_needed="context",
                       spec_json=json.dumps({"acceptable_roles": ["context"]})),
        MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.2", beat_id="B01",
                       function="human_connection", entity_key="judge_pratt", role_needed="evidence",
                       spec_json=json.dumps({"acceptable_roles": ["evidence", "context"]})),
        MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B02.1", beat_id="B02",
                       function="orientation", entity_key="county_court", role_needed="context",
                       spec_json=json.dumps({"acceptable_roles": ["context"]}))])
    hit = _asset(db_session, case, asset_type="photo", found_for="Miami County courthouse Troy")
    _asset(db_session, case, asset_type="photo", found_for="Judge Pratt portrait")
    _asset(db_session, case, asset_type="photo", found_for="something else entirely")
    db_session.commit()
    out = L.build_gap_candidates(db_session, plan, {"entities": [court, judge], "beats": []}, {"B01"})
    rows = db_session.query(MvpCandidate).join(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id).all()
    assert out["created"] == 1 and rows[0].visual_asset_id == hit.id
    assert db_session.get(MvpRequirement, rows[0].requirement_id).requirement_key == "B01.1"
