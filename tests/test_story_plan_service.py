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
    import app.agents.story as story_mod
    from tests.factories import ScriptGen

    fake = ScriptGen()
    fake.structured["story_director"] = _director(3)
    monkeypatch.setattr(story_mod, "get_generation_provider", lambda: fake)
    monkeypatch.setitem(ai_config.language_pipeline.words_per_minute, "fa", 120)
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
    users = [json.loads(c[2]) for c in director.calls if c[1] == "story_director"]
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








def test_prompt_sha256_is_the_hash_of_the_file_bytes():
    import hashlib
    from pathlib import Path

    from app.core.prompts import prompt_sha256

    raw = Path("prompts/language_pipeline/draft_writer.md").read_bytes()
    assert prompt_sha256("language_pipeline/draft_writer") == hashlib.sha256(raw).hexdigest()
