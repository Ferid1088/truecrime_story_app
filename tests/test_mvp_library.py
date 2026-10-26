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
