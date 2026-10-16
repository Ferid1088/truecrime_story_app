"""Part 5a: storing, previewing, editing, redoing and approving plans; the
writer refuses to run without an approved plan; the written story is checked
against the plan."""

import asyncio
import json
import uuid

import pytest

from app.core.ai_config import ai_config
from app.db.models import Case, Fact, StoryPlan
from app.services.story_plan_service import StoryPlanError, StoryPlanService
from app.utils import slugify


def _case(db):
    title = f"Plan {uuid.uuid4().hex[:8]}"
    case = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(case)
    db.commit()
    for i in range(3):
        db.add(Fact(case_id=case.id, claim=f"fact {i}", category="context", confidence=0.9,
                    people_json=json.dumps(["Anna"] if i == 0 else [])))
    db.commit()
    return case


def _director(case_facts):
    ids = [f"F{i:03d}" for i in range(1, case_facts + 1)]

    def design(system, user):
        return {"title": "t", "central_question": "q", "opening_strategy": "critical_moment",
                "acts": [{"id": f"act{n}", "title": f"Act {n}", "purpose": f"p{n}",
                          "target_words": 400, "evidence_ids": ids[n - 1:n]} for n in (1, 2, 3)]}
    return design


@pytest.fixture
def director(monkeypatch):
    from tests.test_master_pipeline import ScriptGen
    import app.agents.story as story_mod

    fake = ScriptGen()
    fake.structured["master_story_director"] = _director(3)
    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: fake)
    monkeypatch.setattr(ai_config.story, "words_per_minute", 120)
    return fake


def _create(db, case, minutes=10):
    return asyncio.run(StoryPlanService(db).create(case, minutes, "fa"))


def test_a_plan_is_stored_enriched_and_validated(db_session, director):
    case = _case(db_session)
    plan = _create(db_session, case)
    data = json.loads(plan.plan_json)
    assert plan.status == "draft" and plan.version == 1 and plan.evidence_fingerprint
    assert [a["id"] for a in data["acts"]] == ["act1", "act2", "act3"]
    assert data["host_slots"][0]["position"] == "opening"
    assert "reveals" in data["acts"][0]
    assert json.loads(plan.validation_json)["status"] in ("valid", "needs_review")


def test_approve_then_the_plan_is_usable_only_for_the_same_film(db_session, director):
    case = _case(db_session)
    service = StoryPlanService(db_session)
    plan = _create(db_session, case)
    assert service.usable_approved(case, "fa", 10) is None          # a draft is not approved
    service.approve(plan, "owner")
    assert service.usable_approved(case, "fa", 10).id == plan.id
    assert service.usable_approved(case, "fa", 12) is None          # other length
    assert service.usable_approved(case, "en", 10) is None          # other language
    db_session.add(Fact(case_id=case.id, claim="new evidence", category="context", confidence=0.9))
    db_session.commit()
    assert service.usable_approved(case, "fa", 10) is None          # evidence moved on


def test_edit_needs_a_fresh_preview_and_supersedes_the_old_plan(db_session, director):
    case = _case(db_session)
    service = StoryPlanService(db_session)
    plan = _create(db_session, case)
    beats = [{"id": "act2", "purpose": "a sharper purpose"}]
    preview = service.preview(plan, "edit", beats=beats)
    assert preview["director_calls"] == 0 and preview["approval_is_lost"] is False
    with pytest.raises(StoryPlanError, match="preview again"):
        service.edit(plan, beats, "stale-token")
    new = service.edit(plan, beats, preview["token"])
    db_session.refresh(plan)
    assert (plan.status, new.status, new.origin, new.supersedes_id) == (
        "superseded", "draft", "edit", plan.id)
    assert json.loads(new.plan_json)["acts"][1]["purpose"] == "a sharper purpose"


def test_redo_runs_the_director_with_the_instruction(db_session, director):
    case = _case(db_session)
    service = StoryPlanService(db_session)
    plan = _create(db_session, case)
    preview = service.preview(plan, "redo", instruction="open on the court")
    assert preview["director_calls"] == 1
    new = asyncio.run(service.redo(plan, "open on the court", preview["token"]))
    assert new.origin == "redo" and new.instruction == "open on the court"
    users = [json.loads(c[2]) for c in director.calls if c[1] == "master_story_director"]
    assert users[-1]["editorial_context"]["user_instruction"] == "open on the court"


def test_an_invalid_plan_cannot_be_approved(db_session, director):
    case = _case(db_session)
    service = StoryPlanService(db_session)
    plan = _create(db_session, case)
    bad = service.edit(plan, [{"id": "act1", "evidence_ids": ["F999"]}],
                       service.preview(plan, "edit", beats=[{"id": "act1", "evidence_ids": ["F999"]}])["token"])
    with pytest.raises(StoryPlanError, match="invalid"):
        service.approve(bad, "owner")


def test_ensure_approved_plans_by_itself_only_when_the_config_allows(db_session, director, monkeypatch):
    case = _case(db_session)
    service = StoryPlanService(db_session)
    with pytest.raises(StoryPlanError, match="required"):
        asyncio.run(service.ensure_approved(case, 10, "fa"))
    monkeypatch.setattr(ai_config.story_plan, "automatic_approval_in_pipeline", True)
    plan = asyncio.run(service.ensure_approved(case, 10, "fa"))
    assert (plan.status, plan.approved_by) == ("approved", "automatic")
    assert asyncio.run(service.ensure_approved(case, 10, "fa")).id == plan.id   # reused


def test_the_master_endpoint_refuses_to_write_without_an_approved_plan(client, db_session, monkeypatch):
    from app.api import masters

    async def authorized():
        return {"authorized": True}

    monkeypatch.setattr(masters.deps, "require_generation_authorized", authorized)
    monkeypatch.setattr(masters, "build_readiness", lambda *a, **k: {
        "master_readiness": {"status": "ready"}})
    monkeypatch.setattr(ai_config.story_plan, "require_approved_plan", True)
    case = _case(db_session)
    r = client.post(f"/api/cases/{case.id}/master-story/generate",
                    json={"target_minutes": 45, "language": "fa"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "story_plan_required"


def test_the_pipeline_writes_to_the_plan_without_a_new_director_call(db_session, monkeypatch):
    from tests.test_master_pipeline import ScriptGen, _pipeline

    fake = ScriptGen()
    fake.structured["master_story_director"] = _director(3)
    pipe = _pipeline(fake, monkeypatch)
    case = _case(db_session)
    service = StoryPlanService(db_session)
    plan = service.approve(asyncio.run(service.create(case, 45, "fa")), "owner")
    directors_before = fake.count("json", "master_story_director")

    from app.agents.story import MASTER_ROLES

    pipe.roles = MASTER_ROLES
    version = asyncio.run(pipe.run(
        db=db_session, case=case, target_minutes=45, language="fa", tone="documentary",
        iterations=0, kind="master",
        words_per_minute=ai_config.words_per_minute_for("fa"), story_plan=plan))
    assert fake.count("json", "master_story_director") == directors_before   # plan reused
    assert version.story_plan_id == plan.id
    structure = json.loads(version.narrative_structure)
    assert structure["story_plan_id"] == plan.id
    assert [a["id"] for a in structure["acts"]] == ["act1", "act2", "act3"]
    gates = json.loads(version.critic_notes)["quality_gates"]
    assert "plan_check" in gates
