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


def test_toolkit_invoke_is_a_noop_when_disabled(db_session, monkeypatch):
    assert ai_config.detective_toolkit.enabled is True, "the toolkit is on by default: it plans the film first"
    monkeypatch.setattr(ai_config.detective_toolkit, "enabled", False)
    case, blueprint, graph = _graph_with_narration(db_session)

    def _boom():
        raise AssertionError("the Detective Director must never be called while the flag is off")
    monkeypatch.setattr(director, "get_generation_provider", _boom)

    result = asyncio.run(toolkit.invoke(db_session, case, blueprint, graph))
    assert result.enabled is False
    assert result.called is False
    assert db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() == 0


def test_pipeline_and_needs_never_import_the_director_module_directly():
    """Bypass-regression check (A4): `app.documentary.detective.director` may only be imported
    from `app.documentary.detective.toolkit` (and tests) — never from pipeline/needs code, which
    would let a caller reach the Detective Director around the single observable boundary."""
    repo_root = Path(__file__).resolve().parent.parent
    guarded_files = [
        repo_root / "app" / "documentary" / "mvp" / "pipeline.py",
        repo_root / "app" / "documentary" / "mvp" / "direct.py",
        repo_root / "app" / "documentary" / "mvp" / "needs.py",
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
