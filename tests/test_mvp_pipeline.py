import asyncio
import json
from uuid import uuid4

from app.core.concurrency import slot
from app.providers.generation.base import GenerationResult
from app.db.models import (Case, EditorialBlueprint, MvpCandidate, MvpRequirement, StoryVersion,
                           VisualAsset, VisualPlan)
from app.documentary.mvp import pipeline as P
from app.documentary.mvp import verify as V


def test_verification_does_not_deadlock_when_candidates_outnumber_the_vision_slots(db_session, monkeypatch):
    """The provider takes a vision slot for every picture call; the stage must not hold one as well."""
    from app.core import concurrency

    monkeypatch.setattr(concurrency.ai_config.concurrency, "vision", 2)
    case = Case(canonical_title="Deadlock proof", slug=f"dl-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    db_session.add(plan)
    db_session.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", entity_key="e", role_needed="evidence", priority="high",
                         spec_json="{}")
    db_session.add(req)
    db_session.commit()
    for i in range(8):
        a = VisualAsset(case_id=case.id, asset_code=f"V{i}", local_path="x.jpg", rights_status="public_domain")
        db_session.add(a)
        db_session.commit()
        db_session.add(MvpCandidate(requirement_id=req.id, visual_asset_id=a.id))
    db_session.commit()

    async def provider_like_judge(case_, asset, entities, facts, agent, db=None):
        async with slot("vision"):          # what the real provider does around every call
            await asyncio.sleep(0.01)
        return V.Verdict(False, {"error": "scripted"})

    monkeypatch.setattr(V, "model_judge", provider_like_judge)
    monkeypatch.setattr(P.ND, "build_pack", lambda db, case_id: ({"facts": []}, []))
    monkeypatch.setattr(P, "entities_with_dossiers", lambda db, case_, reqs: [])
    out = asyncio.run(asyncio.wait_for(
        P.verify_pending(db_session, case, plan, {"entities": [], "beats": []}), 20))
    assert out["judged"] == 8 and json.dumps(out["by_status"])


def test_a_calls_timeout_never_includes_waiting_for_a_vision_slot(db_session, monkeypatch):
    from app.core import concurrency
    from app.documentary.mvp import verify as verify_module

    monkeypatch.setattr(concurrency.ai_config.concurrency, "vision", 2)
    monkeypatch.setattr(verify_module.ai_config.mvp(), "call_timeout_s", 0.35)
    monkeypatch.setattr(verify_module.ai_config.mvp().verification, "transient_failure_retries", 0)
    case = Case(canonical_title="Queue proof", slug=f"q-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    db_session.add(plan)
    db_session.commit()
    req = MvpRequirement(case_id=case.id, visual_plan_id=plan.id, requirement_key="B01.1", beat_id="B01",
                         function="evidence", entity_key="e", role_needed="evidence", priority="high",
                         spec_json="{}")
    db_session.add(req)
    db_session.commit()
    for i in range(12):
        a = VisualAsset(case_id=case.id, asset_code=f"Q{i}", local_path="x.jpg", rights_status="public_domain")
        db_session.add(a)
        db_session.commit()
        db_session.add(MvpCandidate(requirement_id=req.id, visual_asset_id=a.id))
    db_session.commit()

    async def slow_agent(name, gen, body, **kw):
        async with slot("vision"):          # the provider's own slot; each call takes 0.15 s
            await asyncio.sleep(0.15)
        return {"matches": [{"entity": "e", "relation": "unrelated"}], "confidence": 0.9}, \
            GenerationResult(text="{}", model="m", provider="fake")

    monkeypatch.setattr(V, "run_agent", slow_agent)
    monkeypatch.setattr(V, "get_generation_provider", lambda: object())
    monkeypatch.setattr(V.IM, "data_url", lambda p: "data:image/jpeg;base64,AA==")
    monkeypatch.setattr(V, "image_for_check", lambda a: None)
    monkeypatch.setattr(P.ND, "build_pack", lambda db, case_id: ({"facts": []}, []))
    monkeypatch.setattr(P, "entities_with_dossiers", lambda db, case_, reqs: [{"key": "e", "name": "E", "type": "person"}])
    out = asyncio.run(P.verify_pending(db_session, case, plan, {"entities": [], "beats": []}))
    assert out["by_status"]["transient_failure"] == 0 and out["by_status"]["rejected"] == 12


class _Seg:
    id = 1


def _host_env(db_session, monkeypatch, *, ready_after):
    """A film with two planned host scenes; `ready_after` says how many get a verified avatar once run."""
    from types import SimpleNamespace

    from app.documentary import host as host_module
    from app.documentary import host_scenes
    from app.documentary.production import host_timeline as HT

    case = Case(canonical_title="Host proof", slug=f"hp-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    scenes = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    state = {"ready": [], "run": []}

    class FakeQuery:
        def filter(self, *a):
            return self

        def order_by(self, *a):
            return self

        def all(self):
            return scenes

    monkeypatch.setattr(db_session, "query", lambda *a, **k: FakeQuery())
    monkeypatch.setattr(host_module, "latest_host_segments", lambda db, vid: _Seg())
    monkeypatch.setattr(host_module, "HostDirector", lambda: SimpleNamespace(retime_scene=None))
    monkeypatch.setattr("app.agents.stage_director.StageDirector", lambda: SimpleNamespace(direct=None))

    async def run_scene(db, scene, until, retimer=None, director=None):
        state["run"].append((scene.id, until))
        if len(state["run"]) <= ready_after:
            state["ready"].append({"scene_id": scene.id, "position": "mid", "beat_id": "B02", "seconds": 20.0})

    monkeypatch.setattr(host_scenes, "run_scene", run_scene)
    monkeypatch.setattr(HT, "host_items", lambda db, version: list(state["ready"]))
    monkeypatch.setattr(P.ai_config.host, "required_for_film", True)
    monkeypatch.setattr(P.ai_config.host, "enabled", True)
    monkeypatch.setattr(P.ai_config.avatar, "enabled", True)
    return case, state


def test_the_host_stage_produces_every_planned_scene_before_the_film_is_directed(db_session, monkeypatch):
    case, state = _host_env(db_session, monkeypatch, ready_after=2)
    out = asyncio.run(P.stage_host(db_session, case, type("V", (), {"id": 1})(), "en"))
    assert state["run"] == [(1, "avatar"), (2, "avatar")] and out["scenes"] == 2 and out["host_seconds"] == 40.0


def test_the_host_stage_fails_the_film_when_a_scene_cannot_be_produced(db_session, monkeypatch):
    import pytest

    case, state = _host_env(db_session, monkeypatch, ready_after=1)
    with pytest.raises(RuntimeError, match="no verified avatar"):
        asyncio.run(P.stage_host(db_session, case, type("V", (), {"id": 1})(), "en"))


def test_the_host_stage_is_skipped_only_when_the_host_cannot_appear(db_session, monkeypatch):
    case, state = _host_env(db_session, monkeypatch, ready_after=2)
    monkeypatch.setattr(P.ai_config.avatar, "enabled", False)
    assert asyncio.run(P.stage_host(db_session, case, type("V", (), {"id": 1})(), "en"))["skipped"] is True


def test_a_film_without_the_host_avatar_or_with_missing_music_is_not_review_ready(db_session, monkeypatch):
    monkeypatch.setattr(P.ai_config.host, "required_for_film", True)
    monkeypatch.setattr(P.ai_config.host, "enabled", True)
    monkeypatch.setattr(P.ai_config.avatar, "enabled", True)
    case = Case(canonical_title="Gate proof", slug=f"gp-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    sv = StoryVersion(case_id=case.id, version=1, kind="final", language="en", story_text="t",
                      narrative_angle="a")
    db_session.add(sv)
    db_session.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, blueprint_json="{}")
    db_session.add(bp)
    db_session.commit()
    plan = VisualPlan(case_id=case.id, blueprint_id=bp.id, version=1)
    db_session.add(plan)
    db_session.commit()
    manifest = {"timeline": {"sentences": []}, "mix": {"placements": [{}, {}, {}]}}
    script = {"duration": 10.0, "shots": [], "avatar_used": False, "music": [{}]}
    report = P.stage_coverage(db_session, plan, script, manifest)
    text = " ".join(report["blocking"])
    assert "no host avatar" in text and "3 music placement(s) planned, 1 in the film" in text
    assert report["host"]["required"] is True and report["music"] == {"planned": 3, "in_film": 1}
