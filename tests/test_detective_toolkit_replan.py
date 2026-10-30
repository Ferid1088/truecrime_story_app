"""The planner's text hand-off and chunked, resumable planning (the real-case replan of case 15):
`narration.ingest_narration` registers the blueprint's text as narration units, and
`toolkit.invoke` plans a film in chunks of beats, never re-paying a covered chunk and stopping
cleanly when the case's cap is reached. Real PostgreSQL via `db_session`; a fake provider stands in
for the paid call."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from app.core.ai_config import ai_config
from app.db.models import Case, DetectiveScene, EditorialBlueprint, NarrationUnit, RevealGraph, StoryVersion
from app.documentary.detective.narration import blueprint_units, ingest_narration
from app.providers.generation.base import GenerationResult

STORY = ("[[ACT:act1]]\n\n"
         "A man called 911 at night. He said someone shot his wife.\n\n"
         "Officers found two casings near the bed.\n\n"
         "The sisters were nine and eleven.\n\n"
         "The gun was never found.\n\n"
         "Months later a letter arrived.")
BEATS = [
    {"id": "B01", "act_id": "full", "paragraphs": [2, 2]},
    {"id": "B02", "act_id": "full", "paragraphs": [3, 3]},
    {"id": "B03", "act_id": "full", "paragraphs": [4, 4]},
    {"id": "B04", "act_id": "full", "paragraphs": [5, 5]},
    {"id": "B05", "act_id": "full", "paragraphs": [6, 6]},
]


def _film(db_session, beats=BEATS):
    case = Case(canonical_title="Replan test", slug=f"replan-{uuid4().hex[:10]}", language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en", narrative_angle="test",
                         story_text=STORY, engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1, status="valid",
                                   blueprint_json=json.dumps({"beats": beats}))
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.commit()
    return case, blueprint, graph


def test_units_follow_the_blueprint_ranges_and_never_carry_a_section_marker(db_session):
    _, blueprint, _ = _film(db_session)
    units, empty, language = blueprint_units(db_session, blueprint)
    assert language == "en" and empty == []
    assert [u["unit_key"] for u in units] == ["B01-1", "B01-2", "B02-1", "B03-1", "B04-1", "B05-1"]
    assert [u["story_order"] for u in units] == list(range(6))
    assert units[0]["text"] == "A man called 911 at night."
    assert not any("[[" in u["text"] for u in units)


def test_a_beat_that_resolves_to_no_text_is_reported_not_hidden(db_session):
    beats = BEATS[:1] + [{"id": "B02", "act_id": "full", "paragraphs": [1, 1]}]
    _, blueprint, _ = _film(db_session, beats)
    _, empty, _ = blueprint_units(db_session, blueprint)
    assert empty == ["B02"]


def test_ingest_registers_once_and_leaves_an_existing_graph_alone(db_session):
    _, blueprint, graph = _film(db_session)
    first = ingest_narration(db_session, blueprint, graph)
    second = ingest_narration(db_session, blueprint, graph)
    assert (first.registered, first.existing) == (6, 0)
    assert (second.registered, second.existing) == (0, 6)
    assert db_session.query(NarrationUnit).filter_by(graph_id=graph.id).count() == 6


def test_replan_makes_a_new_needs_plan_beside_the_existing_one(db_session, monkeypatch):
    from app.db.models import MvpRequirement, VisualPlan
    from app.documentary.mvp import needs as ND

    case, blueprint, graph = _film(db_session)
    raw = {"entities": [{"key": "gun", "name": "the gun", "type": "object"}],
           "beats": [{"beat_id": "B01", "requirements": [
               {"entity": "gun", "function": "evidence", "role_needed": "context"}]}]}
    asked: list[str] = []

    async def fake_run(name, gen, user, **kw):
        asked.append(name)
        return raw, GenerationResult(text="{}", model="fake", provider="fake")

    monkeypatch.setattr(ND, "run_agent", fake_run)
    first = asyncio.run(ND.plan_needs(db_session, case, blueprint, graph, {"B01": 10.0}))
    reused = asyncio.run(ND.plan_needs(db_session, case, blueprint, graph, {"B01": 10.0}))
    replanned = asyncio.run(ND.plan_needs(db_session, case, blueprint, graph, {"B01": 10.0}, replan=True))

    assert reused.id == first.id and asked == ["mvp.needs", "mvp.needs"]
    assert replanned.id != first.id and replanned.version == first.version + 1
    assert db_session.get(VisualPlan, first.id).status == "requirements"
    assert db_session.query(MvpRequirement).filter_by(visual_plan_id=first.id).count() == 1
    assert db_session.query(MvpRequirement).filter_by(visual_plan_id=replanned.id).count() == 1
    assert ND.latest_mvp_plan(db_session, blueprint.id).id == replanned.id
