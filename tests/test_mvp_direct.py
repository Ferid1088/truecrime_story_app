import asyncio
import json
from uuid import uuid4

from app.core.ai_config import ai_config
from app.db.models import (Case, EditorialBlueprint, MvpCandidate, MvpRequirement, RevealGraph, RevealNode,
                           StoryVersion, VisualAsset, VisualPlan)
from app.documentary.mvp import direct as D


def _manifest(beat_lengths, sentence_seconds=4.0):
    beats, sentences, t = [], [], 0.0
    for i, length in enumerate(beat_lengths):
        beats.append({"beat_id": f"B{i + 1:02d}", "start": t, "end": t + length})
        s = t
        while s + sentence_seconds <= t + length + 0.001:
            sentences.append({"speech": f"sentence at {s:.0f}", "start": s, "end": s + sentence_seconds - 0.04})
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
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
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
    case, plan = _plan(db_session, 3)
    report = run(D.direct_film(db_session, case, plan, _manifest([60.0]), None, {"B01": 0}, {}))
    shots = json.loads(plan.plan_json)["beats"][0]["shots"]
    assert abs(sum(s["share"] for s in shots) - 1.0) < 1e-3
    coded = [s["asset_id"] for s in shots if s.get("asset_id")]
    assert len(coded) == len(set(coded)) == 3
    assert report["gaps"] and report["gap_seconds"] > 0
    assert all(s["command"] == "BLACK_SCREEN" and s["gap"] for s in shots if "asset_id" not in s)


def test_unapproved_candidates_are_never_planned(db_session):
    case, plan = _plan(db_session, 5, approved=False)
    report = run(D.direct_film(db_session, case, plan, _manifest([30.0]), None, {"B01": 0}, {}))
    assert json.loads(plan.plan_json)["beats"][0]["shots"][0]["command"] == "BLACK_SCREEN"
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
