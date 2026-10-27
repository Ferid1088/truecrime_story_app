from uuid import uuid4

from app.db.models import Case, EditorialBlueprint, MvpCandidate, MvpRequirement, StoryVersion, VisualAsset, VisualPlan
from app.documentary.mvp import coverage as CV
from app.documentary.mvp import direct as D
from app.documentary.mvp import satisfy as SA
from app.documentary.visuals.usage import UsageTracker, asset_facts

LENGTH = 40.0
WINDOW = {"start": 100.0, "end": 140.0}


def _item(code, serves=(), role="evidence", rank=5):
    asset = VisualAsset(case_id=1, asset_code=code, asset_type="photo", description=code, title=code,
                        relevance_tier=2, asset_role=role)
    return D.Item(asset, role, None, rank, list(serves), None)


def _photo(code, seconds):
    return {"command": "NEW_IMAGE", "asset_id": code, "share": seconds / LENGTH, "motion": "SLOW_PUSH", "speed": 1.0}


def _req(key, priority="medium"):
    return {"key": key, "roles": {"evidence"}, "priority": priority, "status": "open"}


def run(shots, items, required):
    beats = [{"beat_id": "B01", "shots": shots}]
    tracker = UsageTracker({i.code: asset_facts(i.asset) for i in items})
    at = WINDOW["start"]
    for s in shots:
        if s.get("asset_id"):
            tracker.add(s["asset_id"], at, at + s["share"] * LENGTH)
        at += s["share"] * LENGTH
    placed = SA.RequirementPass(beats, {"B01": WINDOW}, {"B01": items}, {"B01": required}, tracker,
                                lambda bid, item: False, D.fits, D._shot).run()
    return beats[0]["shots"], placed


def test_an_unmet_requirement_gets_its_approved_picture_in_place_of_a_filler():
    items = [_item("FILL"), _item("KEEP", ["B01.1"]), _item("WANT", ["B01.2"])]
    shots, placed = run([_photo("FILL", 20), _photo("KEEP", 20)], items, [_req("B01.1"), _req("B01.2")])
    assert [s["asset_id"] for s in shots] == ["WANT", "KEEP"] and shots[0]["requirement"] == "B01.2"
    assert placed[0]["replaced"] == "FILL" and round(shots[0]["share"], 3) == 0.5


def test_a_picture_that_alone_satisfies_another_requirement_is_never_replaced():
    items = [_item("KEEP", ["B01.1"]), _item("WANT", ["B01.2"])]
    shots, placed = run([_photo("KEEP", 40)], items, [_req("B01.1"), _req("B01.2")])
    assert [s["asset_id"] for s in shots] == ["KEEP"] and placed == []


def test_graphics_and_engine_pages_are_not_replaced():
    items = [_item("WANT", ["B01.1"])]
    graphic = {"command": "SHOW_GRAPHIC", "share": 1.0, "graphic": {"type": "key_facts"}}
    shots, placed = run([graphic], items, [_req("B01.1")])
    assert shots == [graphic] and placed == []


def _plan(db):
    case = Case(canonical_title="Requirement proof", slug=f"rq-{uuid4().hex[:8]}", language="en")
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


def _requirement(db, case, plan, key, beat, codes):
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key=key, beat_id=beat,
                         function="evidence", role_needed="evidence", priority="medium", status="open")
    db.add(req)
    db.commit()
    for code in codes:
        asset = db.query(VisualAsset).filter_by(case_id=case.id, asset_code=code).first() or VisualAsset(
            case_id=case.id, asset_code=code, asset_type="photo", title=code, relevance_tier=2, asset_role="evidence")
        db.add(asset)
        db.commit()
        db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, role="evidence", final_status="approved"))
    db.commit()


def test_a_requirement_whose_approved_pictures_are_all_used_up_is_a_shortage_not_an_omission(db_session):
    case, plan = _plan(db_session)
    _requirement(db_session, case, plan, "B02.1", "B02", ["A"])         # its only picture is shown twice elsewhere
    _requirement(db_session, case, plan, "B03.1", "B03", ["A", "FREE"])  # one picture is still free
    facts = {"A": asset_facts(db_session.query(VisualAsset).filter_by(asset_code="A").first())}
    tracker = UsageTracker(facts)
    for n in range(tracker.limit("A")):                                   # shown as often as the rules allow
        tracker.add("A", n * 600.0, n * 600.0 + 10.0)
    shots = [{"asset_id": "A", "beat_id": "B01", "start": 0.0, "end": 10.0}]
    report = CV.requirement_coverage(db_session, plan, shots, tracker)
    assert report["shortage_ids"] == ["B02.1"] and report["used_up_ids"] == ["B02.1"]
    assert report["uncovered_ids"] == ["B03.1"]
