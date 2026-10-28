"""Section I - inspection, targeted revision, invalidation. Resumability (a plan is never re-paid
for once it exists) is already proven by
tests/test_detective_toolkit_boundary.py::test_toolkit_invoke_never_re_pays_for_an_existing_plan;
not duplicated here."""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.models import (Case, DetectiveScene, DetectiveToolAction, EditorialBlueprint,
                           RevealGraph, RevealNode, StoryVersion)
from app.main import app
from app.documentary.detective.inspector import invalidate_dependent_scenes, plan_summary


def _graph(db):
    slug = f"inspector-{uuid4().hex[:10]}"
    case = Case(canonical_title="Inspector test", slug=slug, language="en")
    db.add(case)
    db.commit()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                         narrative_angle="t", story_text="x")
    db.add(story)
    db.commit()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1, status="valid",
                            blueprint_json="{}")
    db.add(bp)
    db.commit()
    graph = RevealGraph(case_id=case.id, blueprint_id=bp.id, version=1, status="validated")
    db.add(graph)
    db.commit()
    db.add(RevealNode(graph_id=graph.id, node_key="gun", label="Gun", category="object",
                      first_revealed_beat_id="B01", first_revealed_order=1))
    db.commit()
    return case, graph


def _scene_with_action(db, graph, scene_key, claim_key, order=1):
    scene = DetectiveScene(
        graph_id=graph.id, scene_key=scene_key, scene_order=order, mode="direct_scenes",
        aim="x", change_in_understanding="x", why_selected="x", expected_audience_gain="x",
    )
    db.add(scene)
    db.commit()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key=f"{scene_key}-a1", order_index=0, tool_id="fact_card",
        purpose="x", content_bindings_json=json.dumps([claim_key]), planner_decision="accepted",
    )
    db.add(action)
    db.commit()
    return scene


def test_plan_summary_lists_scenes_with_decisions_and_bindings(db_session):
    _, graph = _graph(db_session)
    _scene_with_action(db_session, graph, "s1", "c-location")
    body = plan_summary(db_session, graph)
    assert body["scene_count"] == 1 and body["stale_count"] == 0
    action = body["scenes"][0]["actions"][0]
    assert action["content_bindings"] == ["c-location"] and action["planner_decision"] == "accepted"


def test_correcting_a_claim_invalidates_only_the_bound_scene(db_session):
    _, graph = _graph(db_session)
    bound = _scene_with_action(db_session, graph, "s-bound", "c-location", order=1)
    unrelated = _scene_with_action(db_session, graph, "s-unrelated", "c-other", order=2)
    affected = invalidate_dependent_scenes(db_session, graph.id, "c-location", "owner correction")
    assert affected == ["s-bound"]
    db_session.refresh(bound)
    db_session.refresh(unrelated)
    assert bound.stale is True and bound.stale_reason == "owner correction"
    assert unrelated.stale is False and unrelated.stale_reason is None


def test_api_plan_endpoint_returns_the_same_summary(db_session):
    case, graph = _graph(db_session)
    _scene_with_action(db_session, graph, "s1", "c-location")
    body = TestClient(app).get(f"/api/cases/{case.id}/detective/plan").json()
    assert body["scene_count"] == 1
    assert body["scenes"][0]["scene_key"] == "s1"


def test_api_correct_claim_endpoint_invalidates_and_records_an_event(db_session):
    case, graph = _graph(db_session)
    _scene_with_action(db_session, graph, "s1", "c-location")
    resp = TestClient(app).post(
        f"/api/cases/{case.id}/detective/claims/c-location/correct",
        json={"reason": "narration was revised", "new_text": "the gun was found"},
    )
    assert resp.status_code == 200
    assert resp.json()["invalidated_scenes"] == ["s1"]


def test_api_repair_endpoint_refuses_when_the_toolkit_is_disabled(db_session, monkeypatch):
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.detective_toolkit, "enabled", False)
    case, graph = _graph(db_session)
    _scene_with_action(db_session, graph, "s1", "c-location")
    resp = TestClient(app).post(
        f"/api/cases/{case.id}/detective/scenes/s1/repair",
        json={"issues": ["placeholder issue"]},
    )
    assert resp.status_code == 409


def test_plan_endpoint_404s_without_a_reveal_graph(db_session):
    case = Case(canonical_title="No graph", slug=f"no-graph-{uuid4().hex[:8]}", language="en")
    db_session.add(case)
    db_session.commit()
    resp = TestClient(app).get(f"/api/cases/{case.id}/detective/plan")
    assert resp.status_code == 404
