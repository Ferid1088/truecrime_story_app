import asyncio
import json
from uuid import uuid4

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    EditorialBlueprint,
    MvpCandidate,
    MvpRequirement,
    RevealGraph,
    RevealNode,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary.mvp import direct as D


def _manifest(beat_lengths, sentence_seconds=4.0):
    beats, sentences, t = [], [], 0.0
    for i, length in enumerate(beat_lengths):
        beats.append({"beat_id": f"B{i + 1:02d}", "start": t, "end": t + length})
        s = t
        while s + sentence_seconds <= t + length + 0.001:
            sentences.append({"speech": f"Flynn house at {s:.0f}", "start": s, "end": s + sentence_seconds - 0.04})
            s += sentence_seconds
        t += length
    return {"duration_seconds": t, "timeline": {"beats": beats, "sentences": sentences}}


def test_slots_respect_max_still_and_min_seconds():
    cfg = ai_config.mvp().shots
    window = D.beat_windows(_manifest([60.0]))[0]
    slots = D.make_slots(window)
    lengths = [s["end"] - s["start"] for s in slots]
    assert max(lengths) <= cfg.max_still_seconds + 1e-6 and min(lengths) >= cfg.min_seconds - 1e-6
    assert abs(sum(lengths) - 60.0) < 1e-6 and slots[0]["start"] == 0.0


def _plan(db, n_assets, beats=2, approved=True, reveals=None):
    case = Case(canonical_title="Direct proof", slug=f"dr-{uuid4().hex[:8]}", language="en")
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
                      requirements_json=json.dumps({"entities": [{"key": "e", "type": "building",
                                                                  "name": "Flynn house"}], "beats": []}))
    db.add(plan)
    db.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", entity_key="e", role_needed="evidence", priority="high",
                         spec_json="{}")
    db.add(req)
    db.commit()
    for i in range(n_assets):
        a = VisualAsset(case_id=case.id, asset_code=f"V{i:03d}", local_path="x.jpg", asset_type="photo",
                        reveals_json=json.dumps(reveals or []))
        db.add(a)
        db.commit()
        db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=a.id, role="evidence",
                            final_status="approved" if approved else "rejected"))
    db.commit()
    return case, plan


def run(coro):
    return asyncio.run(coro)


def test_shares_sum_to_one_assets_are_unique_in_a_beat_and_gaps_are_reported(db_session):
    case, plan = _plan(db_session, 2)
    report = run(D.direct_film(db_session, case, plan, _manifest([60.0]), None, {"B01": 0}, {}))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    assert abs(sum(s["share"] for s in shots) - 1.0) < 1e-3
    coded = [s["asset_id"] for s in shots if s.get("asset_id")]
    assert len(coded) == len(set(coded)) == 2
    assert all(s_["share"] * 60 <= 20.5 for s_ in shots if "asset_id" in s_)
    assert report["gaps"] and report["gap_seconds"] > 0
    uncovered = [s for s in shots if "asset_id" not in s]
    assert uncovered and all(s.get("gap") or s["command"] in ("SHOW_MAP", "SHOW_CARD") for s in uncovered)


def test_unapproved_candidates_are_never_planned(db_session):
    case, plan = _plan(db_session, 5, approved=False)
    report = run(D.direct_film(db_session, case, plan, _manifest([30.0]), None, {"B01": 0}, {}))
    assert all("asset_id" not in s for s in json.loads(plan.plan_json)["beats"][0]["shots"])
    assert report["assets_used"] == 0


def test_agent_assignment_is_used_when_valid_and_ignored_when_it_breaks_a_rule(db_session):
    case, plan = _plan(db_session, 6)
    good = {"called": 0}

    async def director(case, beat, slots, pool):
        good["called"] += 1
        return [{"slot": 0, "asset": pool[-1]["asset"]}]
    report = run(D.direct_film(db_session, case, plan, _manifest([30.0]), None, {"B01": 0}, {}, director))
    first = json.loads(plan.plan_json)["beats"][0]["shots"][0]
    assert first["asset_id"] == "V005" and report["agent_directed_beats"] == 1

    async def bad(case, beat, slots, pool):
        return [{"slot": 0, "asset": "NOPE"}]
    case2, plan2 = _plan(db_session, 6)
    report2 = run(D.direct_film(db_session, case2, plan2, _manifest([30.0]), None, {"B01": 0}, {}, bad))
    assert report2["agent_directed_beats"] == 0
    assert json.loads(plan2.plan_json)["beats"][0]["shots"][0]["asset_id"] == "V000"


def test_an_asset_that_reveals_a_later_node_is_not_shown_earlier(db_session):
    case, plan = _plan(db_session, 2, reveals=["F9"])
    graph = RevealGraph(case_id=case.id, blueprint_id=plan.blueprint_id, version=1, status="validated")
    db_session.add(graph)
    db_session.commit()
    db_session.add(RevealNode(graph_id=graph.id, node_key="F9", label="late", category="fact",
                              first_revealed_beat_id="B02", first_revealed_order=1))
    db_session.commit()
    run(D.direct_film(db_session, case, plan, _manifest([20.0, 20.0]), graph.id, {"B01": 0, "B02": 1}, {}))
    first_beat = json.loads(plan.plan_json)["beats"][0]
    assert all("asset_id" not in s for s in first_beat["shots"])


def test_unchanged_inputs_reuse_the_saved_direction_without_calling_the_agent(db_session):
    case, plan = _plan(db_session, 6)
    calls = {"n": 0}

    async def director(case, beat, slots, pool):
        calls["n"] += 1
        return [{"slot": 0, "asset": pool[0]["asset"]}]
    manifest = _manifest([30.0])
    first = run(D.direct_film(db_session, case, plan, manifest, None, {"B01": 0}, {}, director))
    second = run(D.direct_film(db_session, case, plan, manifest, None, {"B01": 0}, {}, director))
    assert calls["n"] == 1 and second["fingerprint"] == first["fingerprint"]
    run(D.direct_film(db_session, case, plan, _manifest([40.0]), None, {"B01": 0}, {}, director))
    assert calls["n"] == 2


def test_a_missing_picture_is_covered_by_holding_the_previous_one_within_the_still_limit(db_session):
    case, plan = _plan(db_session, 4)
    report = run(D.direct_film(db_session, case, plan, _manifest([60.0]), None, {"B01": 0}, {}))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    assert all(s["share"] * 60.0 <= ai_config.mvp().shots.max_still_seconds + 0.5 for s in shots)
    assert report["gap_seconds"] == 0.0 and all("gap" not in s for s in shots)


def test_a_beats_date_becomes_an_overlay_shot(db_session):
    case, plan = _plan(db_session, 8)
    plan.requirements_json = json.dumps({"entities": [{"key": "e", "type": "building", "name": "Flynn house"}],
                                         "beats": [{"beat_id": "B01", "date_text": "February 16, 2026"}]})
    db_session.commit()
    run(D.direct_film(db_session, case, plan, _manifest([60.0]), None, {"B01": 0}, {}))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    date = next(s for s in shots if s["command"] == "SHOW_DATE")
    assert date["overlay"] == {"kind": "date", "text_en": "February 16, 2026"}
    assert abs(sum(s["share"] for s in shots) - 1.0) < 1e-3


def test_a_second_pass_asks_the_agent_only_about_beats_whose_pool_changed(db_session):
    case, plan = _plan(db_session, 6)
    asked = []

    async def director(case, beat, slots, pool):
        asked.append(beat["beat_id"])
        return [{"slot": 0, "asset": pool[0]["asset"]}]
    manifest = _manifest([30.0, 30.0])
    beats = {"B01": {"beat_id": "B01"}, "B02": {"beat_id": "B02"}}
    run(D.direct_film(db_session, case, plan, manifest, None, {"B01": 0, "B02": 1}, beats, director))
    assert asked == ["B01"]            # B02 has no approved pool of its own
    extra = VisualAsset(case_id=case.id, asset_code="V900", local_path="x.jpg", asset_type="photo")
    db_session.add(extra)
    db_session.commit()
    req = db_session.query(MvpRequirement).filter_by(visual_plan_id=plan.id).one()
    db_session.add(MvpCandidate(requirement_id=req.id, visual_asset_id=extra.id, role="evidence",
                                final_status="approved"))
    db_session.commit()
    asked.clear()
    run(D.direct_film(db_session, case, plan, manifest, None, {"B01": 0, "B02": 1}, beats, director))
    assert asked == ["B01"]            # B01's pool changed; nothing else is asked twice
    asked.clear()
    run(D.direct_film(db_session, case, plan, manifest, None, {"B01": 0, "B02": 1}, beats, director))
    assert asked == []


def test_every_requirement_gets_one_of_its_pictures_before_any_gets_a_second(db_session):
    case, plan = _plan(db_session, 0)
    second = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.2", beat_id="B01",
                            function="orientation", entity_key="e2", role_needed="context", priority="medium",
                            spec_json="{}")
    db_session.add(second)
    plan.requirements_json = json.dumps({"entities": [{"key": "e", "type": "building", "name": "Flynn house"},
                                                       {"key": "e2", "type": "building", "name": "Flynn house"}],
                                         "beats": []})
    db_session.commit()
    first = db_session.query(MvpRequirement).filter_by(visual_plan_id=plan.id, requirement_key="B01.1").one()
    for i, req in enumerate((first, first, first, second)):
        a = VisualAsset(case_id=case.id, asset_code=f"R{i}", local_path="x.jpg", asset_type="photo")
        db_session.add(a)
        db_session.commit()
        db_session.add(MvpCandidate(requirement_id=req.id, visual_asset_id=a.id, role="evidence",
                                    final_status="approved"))
    db_session.commit()
    run(D.direct_film(db_session, case, plan, _manifest([20.0]), None, {"B01": 0}, {}))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    assert "R3" in {s["asset_id"] for s in shots if "asset_id" in s}      # the second requirement is on screen


def test_a_picture_is_never_planned_where_the_films_repetition_rules_forbid_it(db_session):
    from app.documentary.visuals.usage import UsageTracker, asset_facts

    case, plan = _plan(db_session, 2)
    run(D.direct_film(db_session, case, plan, _manifest([40.0, 40.0]), None, {"B01": 0, "B02": 1}, {}))
    shots = [s for b in json.loads(plan.plan_json)["beats"] for s in b["shots"] if "asset_id" in s]
    assets = {a.asset_code: a for a in db_session.query(VisualAsset).filter_by(case_id=case.id)}
    tracker = UsageTracker({c: asset_facts(a) for c, a in assets.items()})
    t = 0.0
    for s in shots:
        end = t + s["share"] * 40.0
        assert tracker.allows(s["asset_id"], t, end)[0] or tracker.continues(s["asset_id"], t)
        tracker.add(s["asset_id"], t, end)
        t = end


def test_a_picture_that_is_not_about_the_words_never_fills_a_slot(db_session):
    case, plan = _plan(db_session, 3)
    for a in db_session.query(VisualAsset).filter_by(case_id=case.id):
        a.description = "a tractor in a barn"
    plan.requirements_json = json.dumps({"entities": [{"key": "e", "type": "person", "name": "Judge Pratt"}],
                                         "beats": []})
    db_session.commit()
    report = run(D.direct_film(db_session, case, plan, _manifest([40.0]), None, {"B01": 0}, {}))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    assert all("asset_id" not in s for s in shots) and report["gap_seconds"] > 0


def test_a_named_place_gets_a_map_once_in_the_film_where_no_picture_covers_it(db_session):
    case, plan = _plan(db_session, 0)
    plan.requirements_json = json.dumps({"entities": [{"key": "e", "type": "place", "name": "Flynn house"}],
                                         "beats": []})
    db_session.commit()
    run(D.direct_film(db_session, case, plan, _manifest([40.0, 40.0]), None, {"B01": 0, "B02": 1}, {}))
    beats = json.loads(plan.plan_json)["beats"]
    maps = [s for b in beats for s in b["shots"] if s["command"] == "SHOW_MAP"]
    assert len(maps) == 1 and maps[0]["map_place"] == "Flynn house"


def test_a_map_is_placed_only_where_the_place_is_spoken_and_geocoded_near_the_case(db_session):
    case, plan = _plan(db_session, 0)
    plan.requirements_json = json.dumps({"entities": [{"key": "e", "type": "place", "name": "Tipp City"}],
                                         "beats": []})
    db_session.commit()
    manifest = _manifest([60.0])
    manifest["timeline"]["sentences"][5]["speech"] = "They drove back to Tipp City that night."
    run(D.direct_film(db_session, case, plan, manifest, None, {"B01": 0}, {}, region="Ohio, United States"))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    maps = [s for s in shots if s["command"] == "SHOW_MAP"]
    assert len(maps) == 1 and maps[0]["map_place"] == "Tipp City, Ohio, United States"
    assert "Tipp City" in maps[0]["said"]


def test_a_bare_stretch_next_to_a_picture_is_absorbed_before_it_is_left_black(db_session):
    shots = [{"command": "NEW_IMAGE", "asset_id": "A", "share": 0.1, "motion": "NONE"},
             {"command": "BLACK_SCREEN", "gap": True, "share": 0.05, "motion": "NONE"}]
    D.absorb_gaps(shots, 100.0)
    assert len(shots) == 1 and abs(shots[0]["share"] - 0.15) < 1e-9
    far = [{"command": "NEW_IMAGE", "asset_id": "A", "share": 0.15, "motion": "NONE"},
           {"command": "BLACK_SCREEN", "gap": True, "share": 0.1, "motion": "NONE"}]
    D.absorb_gaps(far, 100.0)               # 25 s would pass max_still_seconds: the stretch stays bare
    assert len(far) == 2


def _repeat_film(description, said="the victim Caleb walked home alone"):
    """One 1000 s beat: picture A for its first 20 s, then a bare stretch of 380 s, then a closing picture."""
    from app.documentary.visuals.usage import UsageTracker, asset_facts

    assets = [VisualAsset(case_id=1, asset_code=code, asset_type="image", description=description,
                          relevance_tier=2, asset_role="evidence")
              for code in ("A", "Z")]
    items = [D.Item(asset=a, role="evidence", entity=None, rank=n) for n, a in enumerate(assets)]
    tracker = UsageTracker({i.code: asset_facts(i.asset) for i in items})
    tracker.add("A", 0.0, 20.0)
    tracker.add("Z", 400.0, 1000.0)
    beat = {"beat_id": "B01", "shots": [
        {"command": "NEW_IMAGE", "share": 0.02, "asset_id": "A"},
        {"command": "BLACK_SCREEN", "share": 0.38, "gap": True, "said": said},
        {"command": "NEW_IMAGE", "share": 0.6, "asset_id": "Z"}]}
    names = {}
    D.repeat_for_gaps([beat], {"B01": {"beat_id": "B01", "start": 0.0, "end": 1000.0}}, {"B01": items}, tracker,
                      names, lambda bid, item: False)
    return beat["shots"], tracker


def test_a_picture_is_shown_again_only_when_about_the_words_and_twice_at_most():
    shots, tracker = _repeat_film("the victim Caleb on a street")
    repeats = [s["asset_id"] for s in shots if s.get("repeat")]
    assert repeats and len(repeats) == len(set(repeats))          # each picture comes back once, not more
    assert tracker.appearances("A") == 2 and tracker.appearances("Z") == 2
    assert abs(sum(s["share"] for s in shots) - 1.0) < 1e-6
    assert all(s["share"] * 1000.0 <= ai_config.mvp().shots.max_still_seconds + 1e-6 for s in shots if s.get("repeat"))
    assert any(s.get("gap") for s in shots)                       # nothing else may fill the rest of the stretch


def test_a_picture_that_is_not_about_the_words_is_never_repeated_to_fill_a_bare_moment():
    shots, tracker = _repeat_film("a tractor in a barn")
    assert not any(s.get("repeat") for s in shots)
    assert any(s.get("gap") for s in shots)
    assert tracker.appearances("A") == 1


def test_two_showings_are_never_less_than_the_minimum_gap_apart():
    shots, tracker = _repeat_film("the victim Caleb on a street")
    need = ai_config.visual_direction.min_repeat_gap_seconds
    assert need >= 300.0
    for code in ("A", "Z"):
        spans = tracker.spans[code]
        for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
            assert start - end >= need - 1e-6


def _moment_clip(db, case, plan, gap, language="en", seconds=30.0):
    from app.documentary.mvp import moments as MC

    asset = VisualAsset(case_id=case.id, asset_code=f"M{uuid4().hex[:6]}", local_path="x.mp4", asset_type="video",
                        duration_seconds=seconds, clip_start=0.0, clip_end=seconds, relevance_tier=2,
                        asset_role="context", spec_json=json.dumps({"moment": {"order": 0}}))
    db.add(asset)
    db.commit()
    key = MC.moment_key(gap["beat"], gap["said"])
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key=MC.requirement_key(language, key),
                         beat_id=gap["beat"], function="moment", role_needed="context", priority="low",
                         spec_json=json.dumps({"moment": {"key": key, "language": language, "beat": gap["beat"]}}))
    db.add(req)
    db.commit()
    db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=asset.id, role="context", final_status="approved"))
    db.commit()
    return asset


def test_footage_cut_for_a_bare_moment_fills_that_moment_in_that_language_only(db_session):
    case, plan = _plan(db_session, 0)
    plan.validation_json = json.dumps({"mvp": {"language": "en"}})
    db_session.commit()
    first = run(D.direct_film(db_session, case, plan, _manifest([30.0]), None, {"B01": 0}, {}))
    gap = first["gaps"][0]
    other = _moment_clip(db_session, case, plan, gap, language="de")
    again = run(D.direct_film(db_session, case, plan, _manifest([30.0]), None, {"B01": 0}, {}))
    assert again["moment_clips_placed"] == 0 and again["gap_seconds"] == first["gap_seconds"]    # a German clip is not used
    clip = _moment_clip(db_session, case, plan, gap, language="en")
    final = run(D.direct_film(db_session, case, plan, _manifest([30.0]), None, {"B01": 0}, {}))
    shots = [s for b in json.loads(plan.plan_json)["beats"] for s in b["shots"]]
    assert final["moment_clips_placed"] == 1
    assert any(s.get("asset_id") == clip.asset_code and s.get("moment_clip") for s in shots)
    assert all(s.get("asset_id") != other.asset_code for s in shots)
    assert final["gap_seconds"] < first["gap_seconds"]


def test_a_slot_whose_words_match_the_owner_note_is_about_the_picture():
    from types import SimpleNamespace

    from app.documentary.mvp import direct as DR

    asset = SimpleNamespace(description="Woman at a podium", owner_note="Ashley's sister Kayla Keyt")
    item = SimpleNamespace(asset=asset, entity=None)
    assert DR.related(item, "Kayla Keyt read a statement to the court.", {})
    assert not DR.related(item, "The jury returned after two hours.", {})
    asset.owner_note = None
    assert not DR.related(item, "Kayla Keyt read a statement to the court.", {})


def test_a_picture_is_only_placed_where_the_words_are_about_it_and_the_slot_otherwise_stays_bare():
    from types import SimpleNamespace

    from app.documentary.visuals.usage import UsageTracker

    item = SimpleNamespace(code="V1", clip_seconds=None, asset=SimpleNamespace(), entity="e")
    slots = [{"start": 0.0, "end": 6.0, "text": "The jury returned."}, {"start": 6.0, "end": 12.0, "text": "Flynn house."}]
    tracker = UsageTracker({"V1": set()})
    out = D.baseline_assign(slots, [item], tracker, [], lambda i, slot: "Flynn" in slot["text"])
    assert out == [None, item]


def _flagged(**mvp):
    from types import SimpleNamespace

    return SimpleNamespace(validation_json=json.dumps({"mvp": mvp}))


def test_neighbour_cover_needs_the_owners_review_first_and_the_owner_can_withdraw_it():
    assert not D.cover_approved(_flagged())                                                   # nothing yet
    assert D.cover_approved(_flagged(neighbour_cover={"approved": True}))                     # approved by the owner
    assert D.cover_approved(_flagged(reviews=2))                                  # resumed after the review
    assert not D.cover_approved(_flagged(reviews=2, neighbour_cover={"approved": False}))   # withdrawn


def test_the_fallback_waits_for_the_second_review_and_the_general_search_for_the_first(db_session):
    from app.documentary.mvp import pipeline as PL

    case, plan = _plan(db_session, 0)
    assert D.reviews_done(plan) == 0 and not D.cover_approved(plan)
    PL.request_review(db_session, plan)
    assert D.reviews_done(plan) == 1 and not D.cover_approved(plan), "after one review: the general search, not the fallback"
    PL.request_review(db_session, plan)
    assert D.reviews_done(plan) == 2 and D.cover_approved(plan), "after two reviews the fallback may fill what is left"


def test_what_resuming_does_next_depends_on_how_many_reviews_have_been_done():
    from app.documentary.mvp import pipeline as PL

    assert "general photos and videos" in PL.next_round_note(1)
    assert "showing a picture again" in PL.next_round_note(2) and "neighbouring pictures" in PL.next_round_note(2)
