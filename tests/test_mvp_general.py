import asyncio
import json
from uuid import uuid4

from PIL import Image

from app.core.ai_config import ai_config
from app.db.models import Case, EditorialBlueprint, MvpRequirement, StoryVersion, VisualAsset, VisualPlan
from app.documentary import storage
from app.documentary.mvp import general as GI
from app.documentary.mvp import moments as MC

SAID = "She had taken out a life insurance policy a few months before."
GAPS = [{"beat": "B05", "start": 100.0, "end": 112.0, "said": SAID}]
KEY = MC.moment_key("B05", SAID)
ENTITY = GI.entity_key(KEY)


def run(coro):
    return asyncio.run(coro)


def _plan(db):
    case = Case(canonical_title="General proof", slug=f"gi-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t", narrative_angle="a")
    db.add(sv)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db.add(bp)
    db.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1, validation_json="{}")
    db.add(plan)
    db.commit()
    return case, plan


def _found(db, case, tmp_path, monkeypatch):
    """A searcher that 'downloads' three pictures for the moment's entity."""
    monkeypatch.setattr(storage, "resolve", lambda rel: tmp_path / rel)
    made = []

    async def searcher(items):
        for i, name in enumerate(("policy", "office", "form")):
            (tmp_path / f"{name}.jpg").write_bytes(b"")
            Image.new("RGB", (800, 600), (i * 40, 90, 120)).save(tmp_path / f"{name}.jpg")
            made.append(VisualAsset(case_id=case.id, asset_code=f"GI{uuid4().hex[:6]}", asset_type="photo",
                                    title=name, local_path=f"{name}.jpg", entity_key=ENTITY, provider="brave_images",
                                    source_url=f"https://example.org/{name}", found_for=items[0]["query"],
                                    rights_status="editorial_review_required", sha256=uuid4().hex))
        db.add_all(made)
        db.commit()
    return searcher


def _planner(queries):
    async def plan(payload):
        return {"moments": [{"id": m["id"], "queries": queries} for m in payload["moments"]]}
    return plan


def _checker(by_title):
    async def check(payload, images):
        assert len(images) == 1 and payload["said"] == SAID
        return by_title[payload["title"]]
    return check


GOOD = {"faces": False, "fit": "direct", "unusable": False, "sensitive": False, "quality": 0.8, "depicts": "a policy form"}


def test_a_picture_with_a_face_is_rejected_a_clean_fitting_one_is_approved_and_a_loose_one_waits_for_review(
        db_session, tmp_path, monkeypatch):
    case, plan = _plan(db_session)
    checks = {"policy": GOOD, "office": {**GOOD, "faces": True}, "form": {**GOOD, "fit": "loose"}}
    result = run(GI.GeneralImagery(db_session, case, plan.id, "en", _planner(["life insurance policy document"]),
                                   _found(db_session, case, tmp_path, monkeypatch), _checker(checks)).run(GAPS))
    assert (result["approved"], result["needs_review"], result["rejected"], result["faces"]) == (1, 1, 1, 1)
    req = db_session.query(MvpRequirement).filter_by(visual_plan_id=plan.id).one()
    assert req.function == "general" and req.status == "met"
    by_title = {db_session.get(VisualAsset, c.visual_asset_id).title: c.final_status for c in req.candidates}
    assert by_title == {"policy": "approved", "office": "rejected", "form": "needs_review"}


def test_a_checker_that_does_not_say_there_are_no_faces_has_not_looked_and_the_picture_is_rejected(
        db_session, tmp_path, monkeypatch):
    case, plan = _plan(db_session)
    silent = {k: v for k, v in GOOD.items() if k != "faces"}
    result = run(GI.GeneralImagery(db_session, case, plan.id, "en", _planner(["x"]),
                                   _found(db_session, case, tmp_path, monkeypatch),
                                   _checker({"policy": silent, "office": silent, "form": silent})).run(GAPS))
    assert result["approved"] == 0 and result["rejected"] == 3


def test_a_logo_a_wrong_subject_and_a_poor_picture_are_rejected(db_session, tmp_path, monkeypatch):
    case, plan = _plan(db_session)
    checks = {"policy": {**GOOD, "unusable": True}, "office": {**GOOD, "fit": "no", "why": "a beach"},
              "form": {**GOOD, "quality": 0.1}}
    result = run(GI.GeneralImagery(db_session, case, plan.id, "en", _planner(["x"]),
                                   _found(db_session, case, tmp_path, monkeypatch), _checker(checks)).run(GAPS))
    assert result["rejected"] == 3 and result["approved"] == 0


def test_a_moment_with_no_showable_subject_is_recorded_and_not_searched_again(db_session, tmp_path, monkeypatch):
    case, plan = _plan(db_session)
    searched = []

    async def searcher(items):
        searched.append(items)

    first = run(GI.GeneralImagery(db_session, case, plan.id, "en", _planner([]), searcher, _checker({})).run(GAPS))
    again = run(GI.GeneralImagery(db_session, case, plan.id, "en", _planner(["x"]), searcher, _checker({})).run(GAPS))
    assert first["no_subject"] == 1 and again["skipped_done"] == 1 and searched == []


def test_the_searches_are_capped_cleaned_and_never_name_a_person(db_session):
    case, plan = _plan(db_session)
    cfg = ai_config.mvp().general
    gi = GI.GeneralImagery(db_session, case, plan.id, "en")
    batch = [MC.Moment("B05", 100.0, 112.0, SAID)]
    out = gi.queries({"moments": [{"id": KEY, "queries": ["  life   insurance form ", "life insurance form", "", "a" * 500,
                                                           "third", "fourth"]}, {"id": "other", "queries": ["x"]}]}, batch)
    assert out == {KEY: ["life insurance form", "a" * cfg.max_query_chars]} and cfg.queries_per_moment == 2
    prompt = open("prompts/documentary/mvp/general_queries_system.md").read()
    assert "NEVER search for a person" in prompt


def test_searches_reach_brave_name_nothing_and_ask_for_no_footage(db_session, tmp_path, monkeypatch):
    case, plan = _plan(db_session)
    seen = []

    async def searcher(items):
        seen.extend(items)

    run(GI.GeneralImagery(db_session, case, plan.id, "en", _planner(["laptop keyboard close up"]), searcher,
                          _checker({})).run(GAPS))
    assert seen and all(i["kind"] == "exact" and i["footage"] is False and i["entity"] == ENTITY
                        and i["must_name"] == [] for i in seen)


def test_the_run_stops_before_a_paid_call_when_the_cap_is_reached(db_session, tmp_path, monkeypatch):
    case, plan = _plan(db_session)
    from app.services.budget import BudgetService

    monkeypatch.setattr(BudgetService, "snapshot", lambda self, case_id: type("S", (), {"spent_eur": 9.0, "cap_eur": 5.0})())
    result = run(GI.GeneralImagery(db_session, case, plan.id, "en", _planner(["x"]),
                                   _found(db_session, case, tmp_path, monkeypatch), _checker({})).run(GAPS))
    assert result["stopped_for_budget"] is True and result["found"] == 0


def test_the_checker_is_told_to_reject_drawn_images_and_look_alike_settings():
    prompt = open("prompts/documentary/mvp/general_check_system.md").read()
    assert "AI-generated" in prompt and "a viewer would take a stranger's house" in prompt
