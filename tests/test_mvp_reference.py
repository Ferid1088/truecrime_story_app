"""The English film is the reference: a language other than English uses only the pictures and videos approved in the English film's plan."""

from __future__ import annotations

import json
from uuid import uuid4

from app.db.models import Case, EditorialBlueprint, MvpCandidate, MvpRequirement, StoryVersion, VisualAsset, VisualPlan
from app.documentary.mvp import direct as DR
from app.documentary.mvp import reference as REF


def beats(*words):
    return json.dumps({"beats": [{"id": f"B{n:02d}", "words": w} for n, w in enumerate(words, 1)]})


def test_beats_are_given_the_stretch_of_the_story_they_cover_and_matched_by_overlap():
    english = REF.beat_spans(beats(100, 100, 100, 100))
    assert english["B01"] == (0.0, 0.25) and english["B04"] == (0.75, 1.0)
    other = REF.beat_spans(beats(50, 150, 100, 50, 50))          # the same story cut differently
    assert REF.matching_beats(other["B01"], english) == ["B01"]
    assert REF.matching_beats(other["B02"], english) == ["B01", "B02"]
    assert REF.matching_beats(other["B05"], english) == ["B04"]


def _film(db, language, words, case=None):
    case = case or Case(canonical_title="Reference", slug=f"ref-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    version = StoryVersion(case_id=case.id, version=1, kind="final", language=language, story_text="t", narrative_angle="a")
    db.add(version)
    db.commit()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=version.id, blueprint_json=beats(*words))
    db.add(blueprint)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=blueprint.id, version=1, requirements_json="{}",
                      validation_json=json.dumps({"mvp": {"reviews": 0}}))
    db.add(plan)
    db.commit()
    return case, plan


def _candidate(db, case, plan, beat, code, status):
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key=f"{beat}.{code}", beat_id=beat, function="evidence",
                         entity_key="e", role_needed="evidence", priority="high", spec_json="{}")
    asset = db.query(VisualAsset).filter_by(case_id=case.id, asset_code=code).first() or VisualAsset(
        case_id=case.id, asset_code=code, local_path="x.jpg", asset_type="photo", reveals_json="[]")
    db.add_all([req, asset])
    db.commit()
    db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, role="evidence", final_status=status))
    db.commit()


def test_another_language_gets_only_what_the_english_plan_approved_never_what_its_own_plan_approved(db_session):
    case, english = _film(db_session, "en", [100, 100, 100, 100])
    _candidate(db_session, case, english, "B01", "EN_APPROVED_FIRST", "approved")
    _candidate(db_session, case, english, "B04", "EN_APPROVED_LAST", "approved")
    _candidate(db_session, case, english, "B02", "EN_REJECTED", "rejected")
    _, persian = _film(db_session, "fa", [50, 150, 100, 50, 50], case)
    _candidate(db_session, case, persian, "B02", "FA_ONLY_APPROVED", "approved")       # judged by the Persian plan itself
    assert REF.is_reference_film(db_session, persian) and not REF.is_reference_film(db_session, english)
    assert REF.english_needs_plan(db_session, case.id).id == english.id
    pool = DR.approved_items(db_session, persian)
    codes = {beat: [i.code for i in items] for beat, items in pool.items()}
    assert codes == {"B01": ["EN_APPROVED_FIRST"], "B02": ["EN_APPROVED_FIRST"], "B04": ["EN_APPROVED_LAST"], "B05": ["EN_APPROVED_LAST"]}
    assert all(i.serves == [] for items in pool.values() for i in items)
    assert "FA_ONLY_APPROVED" not in {i.code for items in DR.approved_items(db_session, english).values() for i in items}, "nor does it leak into the English film"


def test_a_film_in_another_language_gets_no_requirement_pictures_of_its_own_plan(db_session, monkeypatch):
    from app.documentary.mvp import satisfy as SA
    from app.documentary.production.script import satisfy_requirements

    ran = []
    monkeypatch.setattr(SA.ScriptRequirementPass, "run", lambda self: ran.append(self.required) or [])
    case, english = _film(db_session, "en", [100, 100])
    _, persian = _film(db_session, "fa", [100, 100], case)
    rows = {}
    for key, needs in (("en", english), ("fa", persian)):
        rows[key] = VisualPlan(case_id=case.id, blueprint_id=needs.blueprint_id, version=2, requirements_json="{}",
                               validation_json=json.dumps({"mvp": {"language": key, "needs_plan_id": needs.id}}))
        db_session.add(rows[key])
    db_session.commit()
    _candidate(db_session, case, persian, "B01", "FA_ONLY", "approved")
    script = {"shots": [], "firewall": None}
    assert satisfy_requirements(db_session, rows["fa"], script, {}) == [] and ran == [], "the Persian plan's requirements are not satisfied"
    satisfy_requirements(db_session, rows["en"], script, {})
    assert len(ran) == 1, "the English film's are"
