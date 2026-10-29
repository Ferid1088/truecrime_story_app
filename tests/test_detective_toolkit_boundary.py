"""Architecture correction (2026-10-28): the detective_toolkit call boundary (checklist rows
A4, C5, D5). `app.documentary.detective.toolkit.invoke` must be the only way pipeline code reaches
the Detective Director, gated off by default so no real case's behavior or cost changes."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    DetectiveScene,
    DetectiveToolAction,
    EditorialBlueprint,
    NarrationUnit,
    ReleaseAnchor,
    RevealGraph,
    RevealNode,
    StoryVersion,
)
from app.documentary.detective import director, toolkit
from app.documentary.detective.dispatch import record_planner_decisions
from app.documentary.memory import add_narration_unit
from app.providers.generation.base import GenerationResult


def _graph_with_narration(db_session, beats=("B01",)):
    slug = f"toolkit-boundary-{uuid4().hex[:10]}"
    case = Case(canonical_title="Toolkit boundary test", slug=slug, language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                        narrative_angle="test", story_text="placeholder", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1,
                                   status="valid", blueprint_json=json.dumps(
                                       {"beats": [{"id": b} for b in beats]}))
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    for i, b in enumerate(beats):
        add_narration_unit(db_session, graph.id, f"{b}-1", b, i, "en", f"Narration for {b}.")
    db_session.commit()
    return case, blueprint, graph


class FakeDirectorGen:
    """Counts calls so a test can assert the toolkit never re-pays for an existing plan."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[dict] = []

    async def generate_structured(self, role, system, user, images=None, **kw):
        self.calls.append(json.loads(user))
        return (self.payload, GenerationResult(text="{}", model="fake", provider="fake"))


def _fake_payload(graph):
    return {
        "schema_version": "1.0", "run_id": "run-1", "film_id": f"case-{graph.case_id}",
        "language": "en", "text_version": 1, "mode": "map_film",
        "entities": [{"id": "gun", "type": "object", "name": "9mm handgun", "beat_id": "B01",
                     "order": 1, "aliases": []}],
        "claims": [], "questions": [], "relations": [], "knowledge_events": [],
        "scenes": [{
            "id": "s1", "covered_span_ids": ["B01-1"], "aim": "introduce the gun",
            "active_question_ids": [], "active_thread_ids": ["gun"],
            "change_in_understanding": "a gun is now known", "alternatives_considered": [],
            "why_selected": "fact card", "expected_audience_gain": "a concrete anchor",
            "audio_clarity_findings": [],
            "actions": [{"id": "a1", "tool_id": "fact_card", "purpose": "state the find",
                        "payload": {"sentences": ["A 9mm handgun was found."]},
                        "content_bindings": []}],
        }],
        "requests": [], "affected_scene_ids": [], "checks": {},
    }


def test_toolkit_invoke_is_a_noop_when_disabled(db_session, monkeypatch):
    assert ai_config.detective_toolkit.enabled is False, "the flag must default off"
    case, blueprint, graph = _graph_with_narration(db_session)

    def _boom():
        raise AssertionError("the Detective Director must never be called while the flag is off")
    monkeypatch.setattr(director, "get_generation_provider", _boom)

    result = asyncio.run(toolkit.invoke(db_session, case, blueprint, graph))
    assert result.enabled is False
    assert result.called is False
    assert db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() == 0


def test_toolkit_invoke_maps_and_directs_when_enabled(db_session, monkeypatch):
    case, blueprint, graph = _graph_with_narration(db_session)
    fake = FakeDirectorGen(_fake_payload(graph))
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    monkeypatch.setattr(ai_config.detective_toolkit, "enabled", True)
    monkeypatch.setattr(ai_config.detective_toolkit, "require_buildable", False)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_fill_passes", 0)

    result = asyncio.run(toolkit.invoke(db_session, case, blueprint, graph))

    assert result.enabled is True
    assert result.called is True
    assert len(fake.calls) == 2, "exactly one map_film call and one direct_scenes call"
    assert result.scene_count == db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count()
    assert result.scene_count >= 1


def test_toolkit_invoke_never_re_pays_for_an_existing_plan(db_session, monkeypatch):
    case, blueprint, graph = _graph_with_narration(db_session)
    fake = FakeDirectorGen(_fake_payload(graph))
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    monkeypatch.setattr(ai_config.detective_toolkit, "enabled", True)
    monkeypatch.setattr(ai_config.detective_toolkit, "require_buildable", False)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_fill_passes", 0)

    first = asyncio.run(toolkit.invoke(db_session, case, blueprint, graph))
    calls_after_first = len(fake.calls)
    second = asyncio.run(toolkit.invoke(db_session, case, blueprint, graph))

    assert first.called is True
    assert second.called is False
    assert second.reason.startswith("a current detective plan already covers every beat of this graph")
    assert len(fake.calls) == calls_after_first, "no new agent call on the second invoke"


def test_pipeline_and_needs_never_import_the_director_module_directly():
    """Bypass-regression check (A4): `app.documentary.detective.director` may only be imported
    from `app.documentary.detective.toolkit` (and tests) — never from pipeline/needs code, which
    would let a caller reach the Detective Director around the single observable boundary."""
    repo_root = Path(__file__).resolve().parent.parent
    guarded_files = [
        repo_root / "app" / "documentary" / "mvp" / "pipeline.py",
        repo_root / "app" / "documentary" / "mvp" / "direct.py",
        repo_root / "app" / "documentary" / "mvp" / "alternatives.py",
        repo_root / "app" / "documentary" / "mvp" / "engines.py",
        repo_root / "app" / "documentary" / "mvp" / "coverage.py",
    ]
    for path in guarded_files:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.documentary.detective.director":
                raise AssertionError(f"{path} imports the director module directly — bypasses toolkit.invoke")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "app.documentary.detective.director", (
                        f"{path} imports the director module directly — bypasses toolkit.invoke")


def _scene_with_action(db_session, graph, action_key, covered_unit_keys):
    scene = DetectiveScene(graph_id=graph.id, scene_key=f"scene-{action_key}", scene_order=0,
                           mode="map_film", covered_unit_keys_json=json.dumps(covered_unit_keys),
                           aim="test", change_in_understanding="test", why_selected="test",
                           expected_audience_gain="test")
    db_session.add(scene)
    db_session.flush()
    action = DetectiveToolAction(scene_id=scene.id, action_key=action_key, order_index=0,
                                 tool_id="fact_card", purpose="test")
    db_session.add(action)
    db_session.commit()
    return action


def test_record_planner_decisions_covers_deferred_accepted_and_rejected(db_session):
    case, blueprint, graph = _graph_with_narration(db_session, beats=("B01", "B02"))
    deferred_action = _scene_with_action(db_session, graph, "a-deferred", [])
    accepted_action = _scene_with_action(db_session, graph, "a-accepted", ["B01-1"])
    rejected_action = _scene_with_action(db_session, graph, "a-rejected", ["B02-1"])

    record_planner_decisions(db_session, graph.id, covered_beats={"B01"})

    db_session.refresh(deferred_action)
    db_session.refresh(accepted_action)
    db_session.refresh(rejected_action)
    assert deferred_action.planner_decision == "deferred"
    assert accepted_action.planner_decision == "accepted"
    assert rejected_action.planner_decision == "rejected"
    assert all(a.planner_decision_reason for a in (deferred_action, accepted_action, rejected_action))


def test_the_toolkit_makes_the_reveal_graph_and_the_planner_only_reads_it(db_session, monkeypatch):
    import pytest

    from app.db.models import RevealGraph
    from app.documentary.mvp import pipeline as MVP

    case, blueprint, graph = _graph_with_narration(db_session)
    db_session.query(RevealGraph).filter_by(id=graph.id).delete()
    db_session.flush()
    with pytest.raises(LookupError, match="has not made the reveal graph"):
        asyncio.run(MVP.stage_needs(db_session, case, blueprint))
    assert db_session.query(RevealGraph).filter_by(case_id=case.id).count() == 0, "the planner makes no graph"
    monkeypatch.setattr(toolkit.LF, "build_reveal_graph_payload", lambda db, c, b: {"nodes": [], "edges": []})
    made = {}
    monkeypatch.setattr(toolkit.LF, "create_reveal_graph",
                        lambda db, c, b, payload: made.setdefault("g", graph) and made["g"])
    assert toolkit.ensure_graph(db_session, case, blueprint) is graph and "g" in made
