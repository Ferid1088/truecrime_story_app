import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from app.db.models import Case, EditorialBlueprint, StoryVersion, VisualAsset, VisualPlan
from app.documentary.mvp import direct as D
from app.documentary.mvp import moments as MC
from app.documentary.mvp import topical as TP
from app.documentary.visuals.usage import UsageTracker, asset_facts

SAID = "The judge read the decision and the court fell silent."


def run(coro):
    return asyncio.run(coro)


def _item(code, kind="photo", seconds=None, entity=None, desc="x", moment=None):
    asset = VisualAsset(case_id=1, asset_code=code, asset_type=kind, description=desc, title=code, relevance_tier=2,
                        asset_role="context", duration_seconds=seconds, clip_start=0.0 if seconds else None,
                        clip_end=seconds)
    return D.Item(asset, "context", entity, 5, [], moment)


def _plan(db):
    case = Case(canonical_title="Topical proof", slug=f"tp-{uuid4().hex[:8]}", language="en")
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


GAPS = [{"beat": "B05", "start": 100.0, "end": 110.0, "said": SAID}]
KEY = MC.moment_key("B05", SAID)


def test_the_catalog_has_each_approved_picture_once_and_never_maps_or_moment_clips():
    items = {"B01": [_item("A", entity="killer"), _item("M", kind="map"), _item("C", moment={"beat": "B01"})],
             "B02": [_item("A", entity="killer"), _item("V", kind="video", seconds=8)]}
    catalog = TP.catalog_of(items)
    assert sorted(catalog) == ["A", "V"] and catalog["V"]["kind"] == "video"


def test_only_scores_of_at_least_half_for_known_items_are_kept_best_first_and_remembered(db_session):
    case, plan = _plan(db_session)
    catalog = TP.catalog_of({"B01": [_item("A"), _item("B"), _item("C")]})
    calls = []

    async def matcher(payload):
        calls.append(payload)
        return {"matches": [{"moment": KEY, "items": [
            {"id": "B", "score": 0.4, "why": "weak"}, {"id": "A", "score": 0.9, "why": "the defendant"},
            {"id": "C", "score": 0.6, "why": "a court"}, {"id": "ZZ", "score": 0.99, "why": "unknown"}]}]}

    found = run(TP.TopicMatcher(db_session, case, plan, matcher).run(GAPS, catalog))
    assert [(c, s) for c, s, _w in found[KEY]] == [("A", 0.9), ("C", 0.6)]
    again = run(TP.TopicMatcher(db_session, case, plan, matcher).run(GAPS, catalog))
    assert len(calls) == 1 and again == found                              # kept, not paid for twice
    changed = TP.catalog_of({"B01": [_item("A"), _item("B"), _item("C"), _item("D")]})
    run(TP.TopicMatcher(db_session, case, plan, matcher).run(GAPS, changed))
    assert len(calls) == 2                                                  # a new picture: judge again


def test_a_failed_call_is_not_remembered_and_a_moment_left_out_has_no_match(db_session):
    case, plan = _plan(db_session)
    catalog = TP.catalog_of({"B01": [_item("A")]})

    async def broken(payload):
        raise TimeoutError("slow")

    matcher = TP.TopicMatcher(db_session, case, plan, broken)
    assert run(matcher.run(GAPS, catalog)) == {} and matcher.errors

    async def silent(payload):
        return {"matches": []}

    assert run(TP.TopicMatcher(db_session, case, plan, silent).run(GAPS, catalog)) == {}


def test_no_paid_call_once_the_cost_cap_is_reached(db_session, monkeypatch):
    case, plan = _plan(db_session)
    monkeypatch.setattr("app.documentary.mvp.topical.BudgetService",
                        lambda db: SimpleNamespace(snapshot=lambda cid: SimpleNamespace(spent_eur=300.0, cap_eur=300.0)))
    called = []

    async def matcher(payload):
        called.append(1)
        return {}

    run(TP.TopicMatcher(db_session, case, plan, matcher).run(GAPS, TP.catalog_of({"B01": [_item("A")]})))
    assert not called


def _beat(seconds=10.0, said=SAID):
    return [{"beat_id": "B05", "shots": [{"command": "BLACK_SCREEN", "share": 1.0, "gap": True, "said": said}]}], \
        {"B05": {"start": 100.0, "end": 100.0 + seconds}}


def _place(items, matches, tracker, seconds=10.0, blocked=lambda b, i: False):
    beats, windows = _beat(seconds)
    by_beat = {"B05": items}
    n = D.place_topical(beats, windows, by_beat, matches, tracker, blocked)
    return beats[0]["shots"], n


def _tracker(items):
    return UsageTracker({i.code: asset_facts(i.asset) for i in items})


def test_a_related_picture_fills_the_moment_and_one_not_yet_shown_beats_one_that_returns():
    a, b = _item("A"), _item("B")
    tracker = _tracker([a, b])
    tracker.add("A", 0.0, 10.0)                              # A was shown at the start of the film
    shots, n = _place([a, b], {KEY: [("A", 0.95, "defendant"), ("B", 0.6, "court")]}, tracker)
    assert n == 1 and shots[0]["asset_id"] == "B" and shots[0]["topical"] and not shots[0]["repeat"]


def test_a_picture_returns_only_twice_at_most_and_five_minutes_apart():
    a = _item("A")
    far = _tracker([a])
    far.add("A", 0.0, 10.0)
    shots, n = _place([a], {KEY: [("A", 0.9, "x")]}, far)                      # 100 s later is too soon
    assert n == 0 and shots[0].get("gap")
    tracker = _tracker([a])
    tracker.add("A", 0.0, 10.0)
    beats, windows = _beat()
    windows["B05"] = {"start": 600.0, "end": 610.0}
    assert D.place_topical(beats, windows, {"B05": [a]}, {KEY: [("A", 0.9, "x")]}, tracker, lambda b, i: False) == 1
    assert beats[0]["shots"][0]["repeat"]                                         # 600 s later: allowed, a repeat
    third = _tracker([a])
    third.add("A", 0.0, 10.0)
    third.add("A", 300.0, 310.0)
    beats, windows = _beat()
    windows["B05"] = {"start": 900.0, "end": 910.0}
    assert D.place_topical(beats, windows, {"B05": [a]}, {KEY: [("A", 0.9, "x")]}, third, lambda b, i: False) == 0


def test_a_clip_too_short_a_blocked_picture_and_the_neighbouring_picture_are_never_taken():
    short, blocked, ok = _item("S", kind="video", seconds=3.0), _item("X"), _item("OK")
    tracker = _tracker([short, blocked, ok])
    shots, n = _place([short, blocked, ok], {KEY: [("S", 0.99, "a"), ("X", 0.95, "b"), ("OK", 0.5, "c")]}, tracker,
                      blocked=lambda b, i: i.code == "X")
    assert n == 1 and shots[0]["asset_id"] == "OK"


def test_a_long_bare_stretch_is_cut_into_pieces_each_its_own_picture():
    items = [_item(c) for c in "ABC"]
    tracker = _tracker(items)
    shots, n = _place(items, {KEY: [("A", 0.9, "a"), ("B", 0.8, "b"), ("C", 0.7, "c")]}, tracker, seconds=50.0)
    assert n == 3 and [s["asset_id"] for s in shots] == ["A", "B", "C"]
    assert abs(sum(s["share"] for s in shots) - 1.0) < 1e-6


def test_a_moment_nothing_is_related_to_stays_bare_for_the_next_resort():
    a = _item("A")
    shots, n = _place([a], {}, _tracker([a]))
    assert n == 0 and shots[0].get("gap")


def test_direction_places_the_matches_it_is_given(db_session):
    case, plan = _plan(db_session)
    plan.validation_json = json.dumps({"mvp": {"language": "en"}})
    db_session.commit()
    manifest = {"duration_seconds": 30.0, "timeline": {
        "beats": [{"beat_id": "B01", "start": 0.0, "end": 30.0}],
        "sentences": [{"speech": "The judge read the decision.", "start": 0.0, "end": 14.0},
                      {"speech": "The court fell silent.", "start": 14.0, "end": 30.0}]}}
    first = run(D.direct_film(db_session, case, plan, manifest, None, {"B01": 0}, {}))
    gap = first["gaps"][0]
    asset = VisualAsset(case_id=case.id, asset_code=f"T{uuid4().hex[:6]}", local_path="x.jpg", asset_type="photo",
                        description="the defendant", relevance_tier=2)
    db_session.add(asset)
    db_session.commit()
    assert first["topical_placed"] == 0
    from app.db.models import MvpCandidate, MvpRequirement
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", entity_key="e", role_needed="evidence", priority="high", spec_json="{}")
    db_session.add(req)
    db_session.commit()
    db_session.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, role="evidence",
                                final_status="approved"))
    db_session.commit()
    matches = {MC.moment_key(g["beat"], g["said"]): [(asset.asset_code, 0.8, "the defendant")] for g in first["gaps"]}
    plan.status = "draft"
    final = run(D.direct_film(db_session, case, plan, manifest, None, {"B01": 0}, {}, topical=matches))
    shots = [s for b in json.loads(plan.plan_json)["beats"] for s in b["shots"]]
    assert final["topical_placed"] >= 1 and any(s.get("topical") for s in shots) and gap
