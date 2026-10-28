"""Section H — integrate the plan into production and rendering. H5 (the architecture-boundary
row) fixes a real bypass: `resolve_dispatchable_actions` dispatched a tool action regardless of
the Main Visual Planner's recorded `planner_decision`. H1/H2 characterize what survives
composition and what version consistency currently means given no persisted review state exists
yet. H3/H4 ("runtime loop" repair routing) are the Film Experience Reviewer's job (Section J,
not built yet) and are intentionally not exercised here."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from app.db.models import (
    Case,
    DetectiveScene,
    DetectiveToolAction,
    EditorialBlueprint,
    RevealGraph,
    StoryVersion,
)
from app.documentary import memory as MEM
from app.documentary.detective import design_review as DR
from app.documentary.detective import dispatch as D
from app.documentary.graphics.shots import finalize


def _graph(db_session):
    slug = f"detective-h-{uuid4().hex[:10]}"
    case = Case(canonical_title="Section H test", slug=slug, language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                         narrative_angle="test", story_text="placeholder", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1,
                                   status="valid", blueprint_json=json.dumps({"beats": [{"id": "B01"}]}))
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    return graph, case


def _action(db_session, graph, tool_id="fact_card", planner_decision=None, payload=None):
    unit = MEM.add_narration_unit(db_session, graph.id, unit_key="B01-1", beat_id="B01",
                                  story_order=0, text="Jane was the only witness.", language="en")
    MEM.ensure_entity(db_session, graph.id, "jane", "Jane", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "claim-1", "jane", "role", "Jane was the only witness.",
                 "established_in_text")
    scene = DetectiveScene(
        graph_id=graph.id, scene_key="scene-B01", scene_order=0, mode="direct_scenes",
        covered_unit_keys_json=json.dumps([unit.unit_key]), aim="aim", active_question_keys_json="[]",
        active_entity_keys_json="[]", change_in_understanding="change", alternatives_considered_json="[]",
        why_selected="why", expected_audience_gain="gain", audio_clarity_findings_json="[]")
    db_session.add(scene)
    db_session.flush()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key="action-B01", order_index=0, tool_id=tool_id,
        purpose="emphasize the claim", payload_json=json.dumps(payload or {}),
        content_bindings_json=json.dumps([{"claim_key": "claim-1"}]), continuity_refs_json="[]",
        planner_decision=planner_decision)
    db_session.add(action)
    db_session.flush()
    return scene, action


# --- H5: the planner's recorded decision is authoritative ------------------------------------

@pytest.mark.parametrize("decision", ["rejected", "deferred", None])
def test_unaccepted_action_is_never_dispatched_H5(db_session, decision):
    """Before this fix, resolve_dispatchable_actions ignored planner_decision entirely - a
    rejected or still-undecided action could still reach a real shot just because a builder
    existed for its tool_id. This is the exact bypass H5 exists to prevent."""
    graph, _ = _graph(db_session)
    _action(db_session, graph, planner_decision=decision)
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert resolved[0]["skip"] == "not_accepted_by_planner"
    assert "data" not in resolved[0]
    shots: list[dict] = []
    spans = [{"beat_id": "B01", "start": 0.0, "end": 20.0}]
    records = D.place_detective_graphics(shots, spans, "en", resolved)
    assert records[0].get("result") != "placed"
    assert records[0]["skip"] == "not_accepted_by_planner"
    assert shots == []


@pytest.mark.parametrize("decision", ["accepted", "combined"])
def test_accepted_or_combined_action_still_dispatches_H5(db_session, decision):
    """The fix must not block the two decisions that DO mean 'use this'."""
    graph, _ = _graph(db_session)
    _action(db_session, graph, planner_decision=decision)
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert resolved[0].get("skip") is None
    assert resolved[0]["data"] == {"facts": [{"text": "Jane was the only witness.", "fact_id": "claim-1"}]}


# --- H1: detective intent survives composition's later passes --------------------------------

def test_dispatched_graphic_survives_finalize_graphics_H1(db_session):
    """finalize_graphics resolves cue fractions into real seconds (Section A obstacle 7) - this
    must not drop or rename a detective-placed shot's content once it has real start/end times."""
    graph, _ = _graph(db_session)
    _action(db_session, graph, planner_decision="accepted")
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    shots: list[dict] = []
    spans = [{"beat_id": "B01", "start": 0.0, "end": 20.0}]
    D.place_detective_graphics(shots, spans, "en", resolved)
    before_data = shots[0]["graphic"]["data"]
    before_type = shots[0]["graphic"]["type"]
    dropped = finalize(shots, "en")
    assert shots and shots[0]["graphic"]["type"] == before_type
    assert shots[0]["graphic"]["data"] == before_data
    assert shots[0].get("start") is not None and shots[0].get("end") is not None
    assert not any(d.get("type") == before_type for d in dropped)


# --- H2: version consistency - characterized, not a full caching system ----------------------

def test_no_hidden_staleness_because_nothing_is_cached_yet_H2():
    """Section G's review() call has no persisted/cached result keyed by content - every call
    rebuilds its input fresh, so passing a new text_version always reaches the agent payload
    unchanged by any earlier call. Full cross-session invalidation when a correction changes a
    scene's text/assets/timing is Section I's versioning/caching job (not built yet); this proves
    there is no silent staleness bug to close in the meantime, not that I's job is done."""
    payload_v1 = DR.build_input(
        mode="review", run_id="r1", film_id="f1", scene_id="s1", visual_id="v1", language="en",
        text_version=1, detective_intent={}, tool_kind="fact_card",
        data={"facts": [{"text": "x", "fact_id": "c1"}]}, measured={}, preview_id="p1")
    payload_v2 = DR.build_input(
        mode="review", run_id="r1", film_id="f1", scene_id="s1", visual_id="v1", language="en",
        text_version=2, detective_intent={}, tool_kind="fact_card",
        data={"facts": [{"text": "y", "fact_id": "c1"}]}, measured={}, preview_id="p1")
    assert payload_v1["text_version"] == 1 and payload_v2["text_version"] == 2
    assert payload_v1["visual_spec"]["data"] != payload_v2["visual_spec"]["data"]


# --- H wiring: the design-review seam is real but gated off by default -----------------------

def test_review_resolved_actions_is_a_noop_when_disabled(db_session):
    graph, case = _graph(db_session)
    _action(db_session, graph, planner_decision="accepted")
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    outcomes = asyncio.run(DR.review_resolved_actions(db_session, case.id, resolved, "en"))
    assert outcomes == []
