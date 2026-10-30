"""The run stops before anything is rendered when a gap of the film has no portrait that may be shown."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.db.models import Case, EditorialBlueprint, StoryVersion, VisualAsset, VisualPlan
from app.documentary.drawing import portraits as DRP
from app.documentary.mvp import pipeline as PL
from app.documentary.pipeline.errors import AwaitingPortrait


def _film(db, gaps, photos):
    case = Case(canonical_title="Portraits", slug=f"p-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    needs = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json="{}", requirements_json=json.dumps({
        "entities": [{"key": k, "name": k, "type": "person"} for _, _, k in photos]}))
    db.add(needs)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=2, validation_json=json.dumps({
        "mvp": {"needs_plan_id": needs.id},
        "plan": {"gaps": [{"beat": "B01", "start": a, "end": b, "said": "x"} for a, b in gaps]}}))
    db.add(plan)
    for code, priority, entity in photos:
        db.add(VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", local_path=f"{code}.jpg", draw_priority=priority,
                           entity_key=entity, entity_type="person", spec_json="{}"))
    db.commit()
    return case, plan


MANIFEST = {"duration_seconds": 2700.0, "timeline": {"sentences": [
    {"start": 0.0, "end": 8.0, "display": "A call came in about a house."},
    {"start": 300.0, "end": 310.0, "display": "Ashley Flynn had taught children for years."},
    {"start": 900.0, "end": 910.0, "display": "Alleigha Botner testified."}]}}


def test_a_gap_is_covered_when_a_chosen_photo_of_an_already_named_person_exists(db_session):
    case, plan = _film(db_session, [(400, 430)], [("VIS_A", 1, "ashley_flynn"), ("VIS_B", 2, "alleigha_botner")])
    done = PL.stage_portraits(db_session, case, plan, MANIFEST, "en")
    assert (done["portraits"], done["usable"], done["gaps_checked"]) == (2, 2, 1)
    record = json.loads(plan.validation_json)["mvp"]["portraits"]
    assert record["needed"] == done["needed"] >= 1 and record["uncovered"] == [] and 0.5 <= record["speed"] <= 2.0
    assert [o["code"] for o in record["order"]][0] == "VIS_A", "the planner keeps the owner's priority order"


def test_when_a_spoiler_blocks_every_photo_the_run_stops_and_says_what_to_do(db_session):
    case, plan = _film(db_session, [(100, 130)], [("VIS_A", 1, "ashley_flynn"), ("VIS_B", 2, "alleigha_botner")])
    with pytest.raises(AwaitingPortrait) as stopped:
        PL.stage_portraits(db_session, case, plan, MANIFEST, "en")
    text = str(stopped.value)
    assert text.startswith("Awaiting portrait choice:") and "1:40" in text and "priority 1" in text and "VIS_A" in text


def test_a_film_without_any_chosen_photo_is_refused_because_no_film_is_made_without_the_drawing(db_session):
    case, plan = _film(db_session, [], [])
    with pytest.raises(AwaitingPortrait, match="no film is made without it"):
        PL.stage_portraits(db_session, case, plan, MANIFEST, "en")


def test_a_photo_that_shows_an_injury_is_refused_and_a_person_never_named_is_never_earned(db_session):
    case, plan = _film(db_session, [(400, 430)], [("VIS_A", 1, "ashley_flynn"), ("VIS_B", 2, "someone_never_named")])
    hurt = db_session.query(VisualAsset).filter_by(case_id=case.id, asset_code="VIS_A").one()
    hurt.description = "She lies on the floor with a visible wound."
    db_session.commit()
    pool = DRP.portrait_pool(db_session, case.id, MANIFEST["timeline"]["sentences"], {"ashley_flynn", "someone_never_named"})
    assert pool[0].refused == "it shows an injury" and pool[1].earned_from == float("inf")
    with pytest.raises(AwaitingPortrait):
        PL.stage_portraits(db_session, case, plan, MANIFEST, "en")


def test_a_photo_approved_for_a_place_but_showing_a_person_waits_until_that_person_is_named(db_session):
    case, plan = _film(db_session, [], [("VIS_HOUSE", 1, "ashley_flynn")])
    asset = db_session.query(VisualAsset).filter_by(case_id=case.id, asset_code="VIS_HOUSE").one()
    asset.entity_key, asset.entity_type, asset.sha256 = "flynn_residence", "place", "sha"
    asset.spec_json = json.dumps({"picture_card": {"shows": "Two people in a bedroom.", "sha": "sha",
                                                   "people": [{"entity": "ashley_flynn"}, {"entity": "caleb_flynn"}]}})
    db_session.commit()
    sentences = MANIFEST["timeline"]["sentences"] + [{"start": 500.0, "end": 510.0, "display": "Caleb Flynn was questioned."}]
    pool = DRP.portrait_pool(db_session, case.id, sentences, {"ashley_flynn", "caleb_flynn"})
    assert pool[0].earned_from == 500.0, "every person it shows must have been named first (the later of the two)"
