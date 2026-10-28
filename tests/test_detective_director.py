"""Section C — the Detective Director agent. Validation/persistence tests use `db_session` (real
PostgreSQL, no mocks, per this project's verification rule) with a fake generation provider
standing in for the paid call; the actual live paid-provider proof is run outside pytest (see
docs/detective_toolkit_section_c_design.md) since this repo's test suite always runs under
TRUECRIME_TEST_NO_REAL_PROVIDERS=1 (tests/conftest.py)."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.db.models import (
    Case,
    DetectiveKnowledgeEvent,
    DetectiveScene,
    DetectiveToolAction,
    EditorialBlueprint,
    RevealGraph,
    RevealNode,
    StoryVersion,
)
from app.documentary.detective import director
from app.documentary.detective.persist import persist_director_output
from app.documentary.detective.schema import DirectorOutputError, NarrationIndex, validate_director_output
from app.documentary.memory import MemoryValidationError, add_narration_unit
from app.providers.generation.base import GenerationResult


def _graph(db_session, beats=None):
    slug = f"detective-director-{uuid4().hex[:10]}"
    case = Case(canonical_title="Detective director test", slug=slug, language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(
        case_id=case.id, version=1, kind="final", language="en",
        narrative_angle="test", story_text="placeholder", engagement_score=0.0,
    )
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(
        case_id=case.id, story_version_id=story.id, version=1, status="valid",
        blueprint_json=json.dumps({"beats": beats or []}),
    )
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    return graph


def _entity(db_session, graph, node_key, label, beat_id, order, category="object"):
    node = RevealNode(graph_id=graph.id, node_key=node_key, label=label, category=category,
                       first_revealed_beat_id=beat_id, first_revealed_order=order)
    db_session.add(node)
    db_session.flush()
    return node


class FakeDirectorGen:
    """A minimal real `generate_structured`-shaped fake, in `test_chapters.py`'s `CardGen` style,
    standing in for the paid provider so these tests never spend real money."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    async def generate_structured(self, role, system, user, images=None, **kw):
        self.calls.append(json.loads(user))
        return (self.payload, GenerationResult(text="{}", model="fake", provider="fake"))


def test_schema_rejects_premature_release():
    """A content binding to a claim anchored AFTER the scene's own narration position must be
    flagged — this is the "no future answers" invariant from the doc's prompt."""
    narration = [{"unit_key": "B01-1", "story_order": 1}, {"unit_key": "B05-1", "story_order": 5}]
    anchors = [{"anchor_key": "a-early", "unit_key": "B01-1"}, {"anchor_key": "a-late", "unit_key": "B05-1"}]
    index = NarrationIndex.build(narration, anchors)
    payload = {
        "schema_version": "1.0", "run_id": "r1", "film_id": "f1", "language": "en",
        "text_version": 1, "mode": "direct_scenes",
        "claims": [{"id": "c-late", "entity_ids": ["e1"], "attribute": "x", "text": "x",
                    "basis": "established_in_text", "release_anchor": "a-late"}],
        "scenes": [{"id": "s1", "covered_span_ids": ["B01-1"],
                    "actions": [{"id": "a1", "tool_id": "fact_card", "content_bindings": ["c-late"]}]}],
    }
    violations = validate_director_output(payload, index, known_tool_ids={"fact_card"})
    assert any("premature release" in v for v in violations)


def test_schema_rejects_unknown_tool_id():
    index = NarrationIndex.build([{"unit_key": "B01-1", "story_order": 1}], [])
    payload = {
        "schema_version": "1.0", "run_id": "r1", "film_id": "f1", "language": "en",
        "text_version": 1, "mode": "direct_scenes",
        "scenes": [{"id": "s1", "covered_span_ids": ["B01-1"],
                    "actions": [{"id": "a1", "tool_id": "not_a_real_tool", "content_bindings": []}]}],
    }
    violations = validate_director_output(payload, index, known_tool_ids={"fact_card"})
    assert any("unknown tool_id" in v for v in violations)


def test_host_is_not_a_detective_dispatchable_tool():
    """Owner correction, 2026-10-28: Host is owned by the Main Visual Planner's own Host system,
    never a detective-chosen tool action. The real tool_catalog the director is given must not
    offer "host", and an output that proposes it anyway is rejected as an unknown tool_id — the
    same path as any other invalid tool_id, not a silent drop."""
    from app.documentary.detective.tool_catalog import build_tool_catalog

    catalog = build_tool_catalog()
    tool_ids = {t["tool_id"] for t in catalog}
    assert "host" not in tool_ids
    assert "photo" in tool_ids  # photos remain a legitimate detective choice; only host is excluded

    index = NarrationIndex.build([{"unit_key": "B01-1", "story_order": 1}], [])
    payload = {
        "schema_version": "1.0", "run_id": "r1", "film_id": "f1", "language": "en",
        "text_version": 1, "mode": "direct_scenes",
        "scenes": [{"id": "s1", "covered_span_ids": ["B01-1"],
                    "actions": [{"id": "a1", "tool_id": "host", "content_bindings": []}]}],
    }
    violations = validate_director_output(payload, index, known_tool_ids=tool_ids)
    assert any("unknown tool_id" in v for v in violations)


def test_schema_accepts_clean_payload():
    narration = [{"unit_key": "B01-1", "story_order": 1}]
    anchors = [{"anchor_key": "a1", "unit_key": "B01-1"}]
    index = NarrationIndex.build(narration, anchors)
    payload = {
        "schema_version": "1.0", "run_id": "r1", "film_id": "f1", "language": "en",
        "text_version": 1, "mode": "direct_scenes",
        "claims": [{"id": "c1", "entity_ids": ["e1"], "attribute": "x", "text": "x",
                    "basis": "established_in_text", "release_anchor": "a1"}],
        "scenes": [{"id": "s1", "covered_span_ids": ["B01-1"],
                    "actions": [{"id": "a1", "tool_id": "fact_card", "content_bindings": ["c1"]}]}],
    }
    assert validate_director_output(payload, index, known_tool_ids={"fact_card"}) == []


def test_schema_raises_on_missing_envelope_field():
    with pytest.raises(DirectorOutputError):
        validate_director_output({"mode": "direct_scenes"}, NarrationIndex.build([], []), set())


def test_persist_updates_an_existing_scene_instead_of_silently_skipping_it(db_session):
    """Live-call regression: direct_scenes legitimately re-submits the same scene_key a map_film
    call already created (to add its action detail). The second write must replace the scene's
    fields and actions, not be silently dropped as a no-op duplicate."""
    graph = _graph(db_session, beats=[{"id": "B01"}])
    first = {
        "entities": [], "claims": [], "questions": [], "relations": [], "knowledge_events": [],
        "scenes": [{"id": "s1", "covered_span_ids": [], "aim": "map-only aim",
                    "change_in_understanding": "x", "why_selected": "x", "expected_audience_gain": "x",
                    "actions": []}],
    }
    second = {
        "entities": [], "claims": [], "questions": [], "relations": [], "knowledge_events": [],
        "scenes": [{"id": "s1", "covered_span_ids": [], "aim": "refined aim with real actions",
                    "change_in_understanding": "x", "why_selected": "x", "expected_audience_gain": "x",
                    "actions": [{"id": "a1", "tool_id": "fact_card", "content_bindings": []}]}],
    }
    r1 = persist_director_output(db_session, graph.id, None, "map_film", first)
    r2 = persist_director_output(db_session, graph.id, None, "direct_scenes", second)

    assert r1["scenes"] == 1 and r1["scenes_updated"] == 0
    assert r2["scenes"] == 0 and r2["scenes_updated"] == 1
    scenes = db_session.query(DetectiveScene).filter_by(graph_id=graph.id).all()
    assert len(scenes) == 1
    assert scenes[0].aim == "refined aim with real actions"
    actions = db_session.query(DetectiveToolAction).filter_by(scene_id=scenes[0].id).all()
    assert len(actions) == 1 and actions[0].tool_id == "fact_card"


def test_persist_rejects_duplicate_first_reveal(db_session):
    """Two `introduce` knowledge events for the same subject must be rejected, not silently
    merged — Section B's own invariant (`record_knowledge_event`), exercised through Section C's
    persistence path."""
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _entity(db_session, graph, "gun", "9mm handgun", "B01", 1)
    payload = {
        "entities": [], "claims": [], "questions": [], "relations": [],
        "knowledge_events": [
            {"subject_type": "entity", "subject_id": "gun", "operation": "introduce", "new_state": {}},
            {"subject_type": "entity", "subject_id": "gun", "operation": "introduce", "new_state": {}},
        ],
        "scenes": [],
    }
    with pytest.raises(MemoryValidationError):
        persist_director_output(db_session, graph.id, None, "map_film", payload)


def test_map_film_calls_agent_validates_and_persists(db_session, monkeypatch):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _entity(db_session, graph, "gun", "9mm handgun", "B01", 1)
    add_narration_unit(db_session, graph.id, "B01-1", "B01", 1, "en",
                        "Detectives found a 9mm handgun inside the house.")
    db_session.commit()

    fake_payload = {
        "schema_version": "1.0", "run_id": "run-1", "film_id": f"case-{graph.case_id}",
        "language": "en", "text_version": 1, "mode": "map_film",
        "entities": [{"id": "gun", "type": "object", "name": "9mm handgun", "beat_id": "B01",
                      "order": 1, "aliases": ["the gun"]}],
        "claims": [{"id": "c-origin", "entity_ids": ["gun"], "attribute": "origin",
                    "text": "found inside the house", "basis": "established_in_text",
                    "supporting_span_ids": ["B01-1"]}],
        "questions": [], "relations": [],
        "knowledge_events": [
            {"subject_type": "entity", "subject_id": "gun", "operation": "introduce", "new_state": {}},
            {"subject_type": "claim", "subject_id": "c-origin", "operation": "introduce", "new_state": {}},
        ],
        "scenes": [{
            "id": "s1", "covered_span_ids": ["B01-1"], "aim": "introduce the gun",
            "active_question_ids": [], "active_thread_ids": ["gun"],
            "change_in_understanding": "the audience now knows a gun was found in the house",
            "alternatives_considered": [], "why_selected": "a fact card states it plainly",
            "expected_audience_gain": "a concrete anchor for the investigation",
            "audio_clarity_findings": [],
            "actions": [{"id": "a1", "tool_id": "fact_card", "purpose": "state the find",
                         "payload": {"sentences": ["A 9mm handgun was found inside the house."]},
                         "content_bindings": ["c-origin"]}],
        }],
        "requests": [], "affected_scene_ids": [], "checks": {},
    }
    fake = FakeDirectorGen(fake_payload)
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)

    import asyncio
    result = asyncio.run(director.map_film(db_session, graph.id, language="en", text_version=1))

    assert fake.calls, "the agent was never actually called"
    assert result["counts"]["entities"] == 1
    assert result["counts"]["claims"] == 1
    assert result["counts"]["scenes"] == 1
    scenes = db_session.query(DetectiveScene).filter_by(graph_id=graph.id).all()
    assert len(scenes) == 1
    actions = db_session.query(DetectiveToolAction).filter_by(scene_id=scenes[0].id).all()
    assert len(actions) == 1 and actions[0].tool_id == "fact_card"
    events = db_session.query(DetectiveKnowledgeEvent).filter_by(graph_id=graph.id).all()
    assert len(events) == 2


def test_map_film_rejects_premature_release_without_persisting(db_session, monkeypatch):
    graph = _graph(db_session, beats=[{"id": "B01"}, {"id": "B05"}])
    _entity(db_session, graph, "gun", "9mm handgun", "B01", 1)
    add_narration_unit(db_session, graph.id, "B01-1", "B01", 1, "en", "A gun was found.")
    add_narration_unit(db_session, graph.id, "B05-1", "B05", 2, "en", "It was later traced to a pawn shop.")
    db_session.commit()

    from app.db.models import NarrationUnit
    from app.documentary.memory import add_release_anchor
    unit = db_session.query(NarrationUnit).filter_by(graph_id=graph.id, unit_key="B05-1").first()
    add_release_anchor(db_session, graph.id, "a-late", unit.id, 0, len(unit.text))
    db_session.commit()

    fake_payload = {
        "schema_version": "1.0", "run_id": "run-1", "film_id": f"case-{graph.case_id}",
        "language": "en", "text_version": 1, "mode": "direct_scenes",
        "entities": [], "claims": [{"id": "c-traced", "entity_ids": ["gun"], "attribute": "trace_origin",
                                     "text": "traced to a pawn shop", "basis": "established_in_text",
                                     "release_anchor": "a-late", "supporting_span_ids": ["B05-1"]}],
        "questions": [], "relations": [], "knowledge_events": [],
        "scenes": [{
            "id": "s1", "covered_span_ids": ["B01-1"], "aim": "introduce the gun",
            "active_question_ids": [], "active_thread_ids": ["gun"],
            "change_in_understanding": "premature", "alternatives_considered": [],
            "why_selected": "x", "expected_audience_gain": "x", "audio_clarity_findings": [],
            "actions": [{"id": "a1", "tool_id": "fact_card", "purpose": "x", "payload": {},
                         "content_bindings": ["c-traced"]}],
        }],
        "requests": [], "affected_scene_ids": [], "checks": {},
    }
    fake = FakeDirectorGen(fake_payload)
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)

    import asyncio
    with pytest.raises(director.DirectorValidationFailed) as exc:
        asyncio.run(director.direct_scenes(db_session, graph.id, scene_unit_keys=["B01-1"],
                                            language="en", text_version=1))
    assert any("premature release" in v for v in exc.value.violations)
    assert db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() == 0
