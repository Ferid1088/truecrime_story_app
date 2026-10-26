import asyncio
import json
from uuid import uuid4

from app.core.concurrency import slot
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
