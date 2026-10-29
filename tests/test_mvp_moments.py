import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.models import (
    Case,
    EditorialBlueprint,
    MvpCandidate,
    MvpRequirement,
    StoryVersion,
    VisualAsset,
    VisualPlan,
)
from app.documentary.mvp import direct as D
from app.documentary.mvp import moments as MC
from app.documentary.visuals import pieces as PC
from app.documentary.visuals.usage import UsageTracker, asset_facts

SAID = "Officers approached the house at night and checked the doors."


def run(coro):
    return asyncio.run(coro)


def _case_with_footage(db):
    case = Case(canonical_title="Moment proof", slug=f"mom-{uuid4().hex[:8]}", language="en")
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
    code = f"VS{uuid4().hex[:6]}"
    source = VisualAsset(case_id=case.id, asset_code=code, asset_type="video_source", local_path="x.mp4",
                         sha256="abc", source_url="https://example.org/v", provider="web", title="Bodycam", duration_seconds=300.0, rights_status="public_domain",
                         spec_json=json.dumps({"scene_changes": [100.0, 110.0, 120.0, 140.0]}))
    pieces = []
    for n, (a, b, desc) in enumerate([(100.0, 140.0, "Night bodycam: officers walk up to a house and check a door"),
                                      (200.0, 215.0, "A courtroom with a witness at the stand")]):
        pieces.append(VisualAsset(case_id=case.id, asset_code=f"{code}P{n}", asset_type="video", local_path="x.mp4",
                                  title=f"piece {n}", description=desc, clip_start=a, clip_end=b,
                                  duration_seconds=b - a, rights_status="public_domain", verification_status="verified",
                                  spec_json=json.dumps({"parent": code, "piece": n, "window": [a, b]})))
    db.add_all([source, *pieces])
    db.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B09.1", beat_id="B09",
                         function="evidence", entity_key="house", role_needed="context", priority="high")
    db.add(req)
    db.commit()
    db.add(MvpCandidate(requirement_id=req.id, visual_asset_id=pieces[1].id, final_status="approved", role="context",
                        provenance_status="approved", identity_status="approved", rights_status="cleared",
                        ethics_status="approved", scene_status="approved"))
    db.commit()
    return case, plan, source, pieces


@pytest.fixture(autouse=True)
def _files(monkeypatch, tmp_path):
    proxy = tmp_path / "x.mp4"
    proxy.write_bytes(b"x")
    monkeypatch.setattr("app.documentary.mvp.moments.storage.resolve", lambda rel: proxy if rel else None)
    monkeypatch.setattr("app.documentary.mvp.verify.storage.resolve", lambda rel: proxy if rel else None)
    monkeypatch.setattr("app.documentary.mvp.moments.BudgetService",
                        lambda db: SimpleNamespace(snapshot=lambda cid: SimpleNamespace(spent_eur=1.0, cap_eur=300.0)))

    def store(db, source, proposals):
        made = []
        for p in proposals:
            a = VisualAsset(case_id=source.case_id, asset_code=f"M{uuid4().hex[:8]}", asset_type="video",
                            local_path=source.local_path, title=p["name"], description=p["description"],
                            clip_start=p["start"], clip_end=p["end"], duration_seconds=p["end"] - p["start"],
                            rights_status=source.rights_status, verification_status="unverified",
                            sha256=source.sha256, source_url=source.source_url, provider=source.provider,
                            spec_json=json.dumps({"parent": source.asset_code, "window": [p["start"], p["end"]],
                                                  "cut_by": p["cut_by"], **p["spec_extra"]}))
            db.add(a)
            db.commit()
            made.append(a)
        return made

    monkeypatch.setattr(PC, "store_pieces", store)
    monkeypatch.setattr(PC, "piece_frames", lambda *a, **k: [b"jpg", b"jpg", b"jpg"])


def _cutter(db, case, plan, picker, cutter, checker):
    def frames(proxy, times, width):
        return [b"jpg"] * len(times)

    return MC.MomentCutter(db, case, plan.id, "en", picker=picker, cutter=cutter, checker=checker, frames=frames)


def _fakes(region_code, fit="direct", verdict=None, clips=None):
    calls = {"pick": 0, "cut": 0, "check": 0}

    async def picker(payload):
        calls["pick"] += 1
        return {"picks": [{"moment": m["id"], "regions": [region_code]} for m in payload["moments"]]}

    async def cutter(payload, images):
        calls["cut"] += 1
        return {"clips": clips if clips is not None else [{
            "region": region_code, "start": 101.0, "end": 110.0, "fit": fit, "name": "Officers at a door",
            "description": "Night bodycam of officers at a door", "why": "starts when they arrive"}]}

    async def checker(payload, images):
        calls["check"] += 1
        return verdict or {"fit": "direct", "misleading": False, "clean_start": True, "clean_end": True,
                           "unusable": False, "sensitive": False, "depicts": "officers at a door at night"}

    return picker, cutter, checker, calls


GAPS = [{"beat": "B03", "start": 10.0, "end": 18.0, "said": SAID}]


def test_check_clips_snaps_trims_and_refuses_what_breaks_a_rule(db_session):
    case, plan, source, pieces = _case_with_footage(db_session)
    moment = MC.Moment("B03", 10.0, 18.0, SAID)
    region = MC.Region(pieces[0].asset_code, source, 100.0, 140.0, "n", "d")
    regions = {region.code: region}

    def raw(**kw):
        base = {"region": region.code, "start": 100.2, "end": 112.0, "fit": "direct", "name": "a", "description": "b"}
        return [{**base, **kw}]

    taken: dict = {}
    clip = MC.check_clips(raw(), moment, regions, taken)[0]
    assert clip.start == 100.0 and clip.end == 109.0          # snapped to the scene change; no more than the moment + margin
    assert MC.check_clips(raw(start=100.0, end=101.0), moment, regions, {}) == []          # too short
    assert MC.check_clips(raw(region="nope"), moment, regions, {}) == []
    assert MC.check_clips(raw(fit="no"), moment, regions, {}) == []
    assert MC.check_clips(raw(), moment, regions, {source.asset_code: [(95.0, 105.0)]}) == []   # footage already used
    assert MC.check_clips(raw(name=""), moment, regions, {}) == []


def test_a_moment_gets_a_cut_checked_clip_recorded_as_an_approved_candidate(db_session):
    case, plan, source, pieces = _case_with_footage(db_session)
    picker, cutter, checker, calls = _fakes(pieces[0].asset_code)
    stats = run(_cutter(db_session, case, plan, picker, cutter, checker).run(GAPS, set()))
    assert (stats["cut"], stats["approved"], stats["needs_review"], stats["rejected"]) == (1, 1, 0, 0)
    req = db_session.query(MvpRequirement).filter_by(visual_plan_id=plan.id, function="moment").one()
    assert req.beat_id == "B03" and req.priority == "low" and req.entity_key is None
    cand = db_session.query(MvpCandidate).filter_by(requirement_id=req.id).one()
    assert cand.final_status == "approved"
    asset = db_session.get(VisualAsset, cand.visual_asset_id)
    assert json.loads(asset.spec_json)["cut_by"] == "moment" and json.loads(asset.spec_json)["moment"]["beat"] == "B03"
    assert asset.description == "officers at a door at night"


def test_the_same_moment_is_not_cut_or_paid_for_twice_until_the_footage_changes(db_session):
    case, plan, source, pieces = _case_with_footage(db_session)
    picker, cutter, checker, calls = _fakes(pieces[0].asset_code)
    run(_cutter(db_session, case, plan, picker, cutter, checker).run(GAPS, set()))
    again = run(_cutter(db_session, case, plan, picker, cutter, checker).run(GAPS, set()))
    assert again["skipped_done"] == 1 and calls == {"pick": 1, "cut": 1, "check": 1}
    db_session.add(VisualAsset(case_id=case.id, asset_code="NEW", asset_type="video", local_path="x.mp4", title="n",
                               description="another", clip_start=0.0, clip_end=9.0, duration_seconds=9.0,
                               spec_json=json.dumps({"parent": source.asset_code, "window": [0.0, 9.0]})))
    db_session.commit()
    third = run(_cutter(db_session, case, plan, picker, cutter, checker).run(GAPS, set()))
    assert third["skipped_done"] == 0                           # new footage: worth another look


@pytest.mark.parametrize(("verdict", "status"), [
    ({"fit": "no", "why": "wrong place"}, "rejected"),
    ({"fit": "loose"}, "needs_review"),
    ({"fit": "direct", "sensitive": True}, "needs_review"),
    ({"fit": "direct", "clean_end": False}, "needs_review"),
    ({"fit": "direct", "misleading": True}, "rejected"),
    ({"fit": "direct", "unusable": True}, "rejected"),
])
def test_the_checkers_verdict_decides_whether_a_clip_is_used_reviewed_or_dropped(db_session, verdict, status):
    case, plan, source, pieces = _case_with_footage(db_session)
    picker, cutter, checker, _ = _fakes(pieces[0].asset_code, verdict=verdict)
    stats = run(_cutter(db_session, case, plan, picker, cutter, checker).run(GAPS, set()))
    assert stats[status] == 1
    cand = db_session.query(MvpCandidate).join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id).filter(
        MvpRequirement.function == "moment", MvpRequirement.visual_plan_id == plan.id).one()
    assert cand.final_status == status


def test_nothing_honest_to_cut_leaves_the_moment_bare_and_remembered(db_session):
    case, plan, source, pieces = _case_with_footage(db_session)
    picker, cutter, checker, calls = _fakes(pieces[0].asset_code, clips=[])
    stats = run(_cutter(db_session, case, plan, picker, cutter, checker).run(GAPS, set()))
    assert stats["nothing_cut"] == 1 and stats["cut"] == 0 and calls["check"] == 0
    req = db_session.query(MvpRequirement).filter_by(visual_plan_id=plan.id, function="moment").one()
    assert req.status == "open" and json.loads(req.spec_json)["moment"]["outcome"].startswith("no footage")


def test_footage_the_planner_already_uses_is_never_cut_again(db_session):
    case, plan, source, pieces = _case_with_footage(db_session)
    seen = []
    picker, cutter, checker, calls = _fakes(pieces[0].asset_code)

    async def spy(payload):
        seen.extend(r["id"] for r in payload["regions"])
        return await picker(payload)

    stats = run(_cutter(db_session, case, plan, spy, cutter, checker).run(GAPS, {pieces[0].asset_code}))
    assert pieces[0].asset_code not in seen and pieces[1].asset_code in seen   # footage on screen is never offered
    assert stats["cut"] == 0


def test_no_paid_call_is_made_once_the_cost_cap_is_reached(db_session, monkeypatch):
    case, plan, source, pieces = _case_with_footage(db_session)
    monkeypatch.setattr("app.documentary.mvp.moments.BudgetService",
                        lambda db: SimpleNamespace(snapshot=lambda cid: SimpleNamespace(spent_eur=300.0, cap_eur=300.0)))
    picker, cutter, checker, calls = _fakes(pieces[0].asset_code)
    stats = run(_cutter(db_session, case, plan, picker, cutter, checker).run(GAPS, set()))
    assert stats["stopped_for_budget"] and calls == {"pick": 0, "cut": 0, "check": 0}


def test_a_source_the_rights_block_is_not_used(db_session):
    case, plan, source, pieces = _case_with_footage(db_session)
    source.rights_status = "do_not_use"
    db_session.commit()
    picker, cutter, checker, calls = _fakes(pieces[0].asset_code)
    stats = run(_cutter(db_session, case, plan, picker, cutter, checker).run(GAPS, set()))
    assert calls["pick"] == 0 and stats["cut"] == 0


def _item(code, seconds, beat="B03", order=0, said=SAID):
    asset = VisualAsset(case_id=1, asset_code=code, asset_type="video", duration_seconds=seconds, clip_start=0.0,
                        clip_end=seconds, relevance_tier=2, asset_role="context",
                        spec_json=json.dumps({"moment": {"order": order}}))
    return D.Item(asset, "context", None, 5, [], {"beat": beat, "key": MC.moment_key(beat, said), "language": "en"})


def _beat(seconds=8.0, said=SAID):
    shots = [{"command": "BLACK_SCREEN", "share": 1.0, "gap": True, "said": said}]
    return [{"beat_id": "B03", "shots": shots}], {"B03": {"start": 10.0, "end": 10.0 + seconds}}


def test_a_clip_is_placed_on_the_moment_it_was_cut_for_and_a_short_tail_is_held(db_session):
    beats, windows = _beat(8.0)
    clip = _item("C1", 8.3)
    tracker = UsageTracker({"C1": asset_facts(clip.asset)})
    assert D.place_moment_clips(beats, windows, [clip], tracker) == 1
    shots = beats[0]["shots"]
    assert [s["command"] for s in shots] == ["SHOW_CLIP"] and abs(shots[0]["share"] - 1.0) < 1e-6


def test_two_clips_follow_each_other_and_a_long_rest_stays_bare():
    beats, windows = _beat(14.0)
    clips = [_item("C1", 5.0, order=0), _item("C2", 5.0, order=1)]
    tracker = UsageTracker({c.code: asset_facts(c.asset) for c in clips})
    assert D.place_moment_clips(beats, windows, clips, tracker) == 2
    shots = beats[0]["shots"]
    assert [s["command"] for s in shots] == ["SHOW_CLIP", "SHOW_CLIP", "BLACK_SCREEN"]
    assert abs(sum(s["share"] for s in shots) - 1.0) < 1e-6 and shots[2].get("gap")


def test_a_clip_for_another_moment_or_beat_is_never_placed_here():
    beats, windows = _beat(8.0, said="Something else entirely was said here.")
    clip = _item("C1", 9.0)
    tracker = UsageTracker({"C1": asset_facts(clip.asset)})
    assert D.place_moment_clips(beats, windows, [clip], tracker) == 0
    assert beats[0]["shots"][0].get("gap")


def test_the_batches_of_moments_are_picked_at_the_same_time_and_a_failed_batch_is_tried_again_next_time(db_session, monkeypatch):
    import time

    monkeypatch.setattr(MC.ai_config.mvp().moments, "picks_per_call", 1)
    case, plan, source, pieces = _case_with_footage(db_session)
    gaps = [{"beat": f"B{n:02d}", "start": 10.0 * n, "end": 10.0 * n + 8, "said": f"{SAID} variant {n}"}
            for n in range(1, 5)]
    failing = MC.moment_key(gaps[1]["beat"], gaps[1]["said"])

    async def picker(payload):
        await asyncio.sleep(0.3)
        if payload["moments"][0]["id"] == failing:
            raise RuntimeError("model down")
        return {"picks": []}

    _, cutter, checker, calls = _fakes(pieces[0].asset_code)
    cut = _cutter(db_session, case, plan, picker, cutter, checker)
    started = time.monotonic()
    run(cut.run(gaps, set()))
    elapsed = time.monotonic() - started

    assert elapsed < 0.9, f"four picks of 0.3 s ran one after another ({elapsed:.2f}s)"
    assert any("model down" in e for e in cut.stats.errors) and calls["cut"] == 0
    assert db_session.query(MvpRequirement).filter_by(visual_plan_id=plan.id, function="moment").count() == 3, \
        "the three moments nothing was picked for are recorded; the failed one is not, so it is tried again"
