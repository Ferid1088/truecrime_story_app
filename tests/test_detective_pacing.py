"""Checklist row C3 - the structural half of "question payoffs, meaningful connections, human interest and
breathing room follow the text", checked deterministically from a persisted plan (real PostgreSQL). The same
checker is run on the real Director output for case 15's B13-B20 in
docs/detective_toolkit_section_f2f3c3_design.md."""

from __future__ import annotations

import json
from uuid import uuid4

from app.core.ai_config import ai_config
from app.db.models import Case, DetectiveScene, DetectiveToolAction, EditorialBlueprint, RevealGraph, StoryVersion
from app.documentary import memory as MEM
from app.documentary.detective import pacing as P


def _graph(db_session):
    case = Case(canonical_title="Pacing test", slug=f"pacing-{uuid4().hex[:10]}", language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en", narrative_angle="t",
                         story_text="p", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1, status="valid",
                            blueprint_json=json.dumps({"beats": [{"id": "B01"}]}))
    db_session.add(bp)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=bp.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    MEM.ensure_entity(db_session, graph.id, "gun", "the gun", "object", "B01", 0)
    return graph


def _question(db_session, graph, key, kind, *events):
    MEM.add_question(db_session, graph.id, question_key=key, entity_node_keys=["gun"], text=f"{key}?",
                     kind=kind)
    MEM.record_knowledge_event(db_session, graph.id, "question", key, "introduce", {"status": "open"})
    for operation, status in events:
        MEM.record_knowledge_event(db_session, graph.id, "question", key, operation, {"status": status})


def _scene(db_session, graph, order, *actions):
    """actions: (action_key, tool_id, bindings, reuse_reason) tuples; none = a quiet hold scene."""
    scene = DetectiveScene(
        graph_id=graph.id, scene_key=f"scn{order}", scene_order=order, mode="direct_scenes",
        covered_unit_keys_json="[]", aim="a", active_question_keys_json="[]", active_entity_keys_json="[]",
        change_in_understanding="c", alternatives_considered_json="[]", why_selected="w",
        expected_audience_gain="g", audio_clarity_findings_json="[]")
    db_session.add(scene)
    db_session.flush()
    for i, (key, tool, bindings, reuse) in enumerate(actions):
        db_session.add(DetectiveToolAction(
            scene_id=scene.id, action_key=key, order_index=i, tool_id=tool, purpose="p", payload_json="{}",
            content_bindings_json=json.dumps(bindings), continuity_refs_json="[]", reuse_reason=reuse))
    db_session.flush()


def test_each_honest_ending_is_told_from_a_forgotten_question(db_session):
    graph = _graph(db_session)
    _question(db_session, graph, "q-answered", "not_yet_explained", ("resolve", "answered"))
    _question(db_session, graph, "q-qualified", "not_yet_explained", ("update", "partial"))
    _question(db_session, graph, "q-reframed", "interpretive_question", ("reframe", "reframed"))
    _question(db_session, graph, "q-open-by-text", "explicit_unresolved")
    _question(db_session, graph, "q-forgotten", "not_yet_explained")
    endings = {p["question_key"]: p["ending"] for p in P.question_payoffs(db_session, graph.id)}
    assert endings == {"q-answered": "answered", "q-qualified": "qualified", "q-reframed": "qualified",
                       "q-open-by-text": "left_open_by_text", "q-forgotten": "forgotten"}


def test_only_the_forgotten_question_becomes_a_finding(db_session):
    graph = _graph(db_session)
    _question(db_session, graph, "q-answered", "not_yet_explained", ("resolve", "answered"))
    _question(db_session, graph, "q-forgotten", "not_yet_explained")
    findings = P.c3_report(db_session, graph.id)["findings"]
    assert [(f["kind"], f["question_key"]) for f in findings] == [("forgotten_question", "q-forgotten")]


def test_a_later_answer_replaces_an_earlier_state_not_the_other_way_round(db_session):
    graph = _graph(db_session)
    _question(db_session, graph, "q", "not_yet_explained", ("update", "partial"), ("resolve", "answered"))
    assert P.question_payoffs(db_session, graph.id)[0]["ending"] == "answered"


def test_a_return_needs_its_reason_and_a_first_showing_does_not(db_session):
    graph = _graph(db_session)
    _scene(db_session, graph, 0, ("a1", "fact_card", ["c1", "q1"], None))
    _scene(db_session, graph, 1, ("a2", "detective_board", ["c1"], "the answer changes what c1 means"))
    _scene(db_session, graph, 2, ("a3", "detective_board", ["q1"], None))
    assert [r["action_key"] for r in P.returns_without_reason(db_session, graph.id)] == ["a3"]
    assert P.returns_without_reason(db_session, graph.id)[0]["first_shown_by"] == ["a1"]


def _dense_scene_run(db_session, graph, count, first_order=0, tool="fact_card"):
    for i in range(count):
        _scene(db_session, graph, first_order + i, (f"d{first_order + i}", tool, [], None))


def test_a_run_of_text_dense_actions_with_no_pause_is_flagged(db_session):
    graph = _graph(db_session)
    limit = ai_config.detective_toolkit.pacing.max_consecutive_dense_actions
    _dense_scene_run(db_session, graph, limit + 1)
    runs = P.dense_runs(db_session, graph.id)
    assert len(runs) == 1 and runs[0]["length"] == limit + 1
    assert P.c3_report(db_session, graph.id)["findings"][0]["kind"] == "dense_run"


def test_a_run_at_the_limit_is_fine(db_session):
    graph = _graph(db_session)
    _dense_scene_run(db_session, graph, ai_config.detective_toolkit.pacing.max_consecutive_dense_actions)
    assert P.dense_runs(db_session, graph.id) == []


def test_a_picture_a_place_or_a_quiet_hold_gives_the_viewer_room(db_session):
    graph = _graph(db_session)
    limit = ai_config.detective_toolkit.pacing.max_consecutive_dense_actions
    _dense_scene_run(db_session, graph, limit)
    _scene(db_session, graph, limit, ("p1", "photo", [], None))                 # a real picture
    _dense_scene_run(db_session, graph, limit, first_order=limit + 1)
    _scene(db_session, graph, 2 * limit + 1)                                    # a scene with no action: a hold
    _dense_scene_run(db_session, graph, limit, first_order=2 * limit + 2)
    assert P.dense_runs(db_session, graph.id) == []


def test_the_pause_tools_come_from_config(db_session, monkeypatch):
    graph = _graph(db_session)
    limit = ai_config.detective_toolkit.pacing.max_consecutive_dense_actions
    _dense_scene_run(db_session, graph, limit)
    _scene(db_session, graph, limit, ("m1", "map", [], None))
    _dense_scene_run(db_session, graph, 1, first_order=limit + 1)
    assert P.dense_runs(db_session, graph.id) == []
    monkeypatch.setattr(ai_config.detective_toolkit.pacing, "breathing_tools", ["photo"])
    assert P.dense_runs(db_session, graph.id) == [{"actions": [f"d{i}" for i in range(limit)] + ["m1", f"d{limit + 1}"],
                                                   "length": limit + 2}]


def test_a_return_lists_each_shown_again_id_once(db_session):
    """Real Director output binds a flat list of IDs of every kind, which both binding lookups return."""
    graph = _graph(db_session)
    _scene(db_session, graph, 0, ("a1", "fact_card", ["c1", "q1"], None))
    _scene(db_session, graph, 1, ("a2", "evidence_check", ["c1", "q1"], None))
    assert P.returns_without_reason(db_session, graph.id)[0]["returns"] == ["c1", "q1"]
