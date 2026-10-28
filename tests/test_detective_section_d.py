"""Section D — executable detective tools. Every gated behavior is proven both ON (a detective
plan exists) and OFF (it doesn't, i.e. every real case today) to guarantee no regression."""

from __future__ import annotations

import json
from uuid import uuid4

from app.db.models import (
    Case,
    DetectiveScene,
    DetectiveToolAction,
    EditorialBlueprint,
    RevealGraph,
    RevealNode,
    StoryVersion,
)
from app.documentary import memory as MEM
from app.documentary.detective import dispatch as D


def _graph(db_session, beats=None):
    slug = f"detective-d-{uuid4().hex[:10]}"
    case = Case(canonical_title="Section D test", slug=slug, language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                         narrative_angle="test", story_text="placeholder", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1,
                                   status="valid", blueprint_json=json.dumps({"beats": beats or []}))
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    return graph, blueprint, case


def _scene_with_action(db_session, graph, beat_id, order, tool_id, purpose="test purpose",
                       payload=None, reuse_reason=None, scene_key=None, active_entity=None,
                       planner_decision="accepted"):
    unit = MEM.add_narration_unit(db_session, graph.id, unit_key=f"{beat_id}-1", beat_id=beat_id,
                                  story_order=order, text="narration text", language="en")
    scene = DetectiveScene(
        graph_id=graph.id, scene_key=scene_key or f"scene-{beat_id}", scene_order=order, mode="direct_scenes",
        covered_unit_keys_json=json.dumps([unit.unit_key]), aim="aim", active_question_keys_json="[]",
        active_entity_keys_json=json.dumps([active_entity] if active_entity else []), change_in_understanding="change",
        alternatives_considered_json="[]", why_selected="why", expected_audience_gain="gain",
        audio_clarity_findings_json="[]")
    db_session.add(scene)
    db_session.flush()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key=f"action-{beat_id}", order_index=0, tool_id=tool_id,
        purpose=purpose, payload_json=json.dumps(payload or {}), content_bindings_json="[]",
        continuity_refs_json="[]", reuse_reason=reuse_reason, planner_decision=planner_decision)
    db_session.add(action)
    db_session.flush()
    return scene, action


def test_has_detective_plan_true_only_when_scenes_exist(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    assert D.has_detective_plan(db_session, graph.id) is False
    _scene_with_action(db_session, graph, "B01", 0, "fact_card")
    assert D.has_detective_plan(db_session, graph.id) is True


def test_fact_card_dispatches_and_carves_a_real_shot_T09(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "person-1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "claim-1", "person-1", "role", "Jane was the only witness.",
                 "established_in_text")
    scene, action = _scene_with_action(db_session, graph, "B01", 0, "fact_card", purpose="emphasize the claim")
    action.content_bindings_json = json.dumps([{"claim_key": "claim-1"}])
    db_session.flush()
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert resolved[0]["data"] == {"facts": [{"text": "Jane was the only witness.", "fact_id": "claim-1"}]}
    shots: list[dict] = []
    spans = [{"beat_id": "B01", "start": 0.0, "end": 20.0}]
    records = D.place_detective_graphics(shots, spans, "en", resolved)
    assert records[0]["result"] == "placed"
    assert shots and shots[0]["kind"] == "graphic" and shots[0]["graphic"]["type"] == "fact_card"
    assert shots[0]["graphic"]["data"] == resolved[0]["data"] and shots[0]["detective_reuse_reason"] is None


def test_floor_plan_dispatches_when_payload_has_real_plan_data_T14(db_session):
    """Section A found `floor_plan` fully implemented but never called (dead). This proves it is
    now reachable via a detective tool action - with a fixture, since no case has real plan
    geometry (the renderer refuses an invented layout by design, see floorplan.py:can_render)."""
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    plan_data = {
        "source": {"document_id": 1, "page": 2}, "rooms": [{"id": "r1", "label": "Kitchen", "x": 0, "y": 0, "w": 10, "h": 10}],
        "markers": [{"kind": "found", "room": "r1", "text": "Knife found here", "fact_id": "claim-1"}],
    }
    _, action = _scene_with_action(db_session, graph, "B01", 0, "floor_plan", payload=plan_data)
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert resolved[0]["data"] == plan_data
    shots: list[dict] = []
    records = D.place_detective_graphics(shots, [{"beat_id": "B01", "start": 0.0, "end": 20.0}], "en", resolved)
    assert records[0]["result"] == "placed" and shots[0]["graphic"]["type"] == "floor_plan"


def test_unsupported_tool_id_is_reported_not_silently_dropped(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    _scene_with_action(db_session, graph, "B01", 0, "courtroom")
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert resolved[0]["skip"] == "not_dispatched_in_this_pass"


def test_fact_card_with_no_bound_claim_is_an_honest_skip_not_a_fabrication(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    _scene_with_action(db_session, graph, "B01", 0, "fact_card")   # no claim bound
    resolved = D.resolve_dispatchable_actions(db_session, graph.id)
    assert resolved[0]["skip"] == "insufficient_data"


def test_obstacle_4_detective_reuse_reason_exempts_a_shot_from_the_repetition_block():
    from app.documentary.mvp.coverage import graphic_checks

    def shot(start, **extra):
        return {"kind": "graphic", "start": start, "end": start + 5.0,
               "graphic": {"type": "fact_card", "language": "en", "data": {"facts": [{"fact_id": "F1"}]}}, **extra}

    script = {"language": "en", "shots": [shot(0.0), shot(10.0)]}
    # Unchanged: true redundant replay is still blocked.
    assert graphic_checks(script)["repeated"]
    # New: a detective-authored deliberate return is not.
    authorized = {"language": "en",
                 "shots": [shot(0.0), shot(10.0, detective_reuse_reason="updates the claim with a new source")]}
    assert graphic_checks(authorized)["repeated"] == []


def test_obstacle_6_detective_board_override_wins_over_dated_event_when_present():
    from app.documentary.detective_board import select_cards

    blueprint = {"beats": [{"id": "B01", "purpose": "contradiction"}]}
    told_events = [{"id": "T1", "first_beat": "B01"}]
    # Unchanged default: the dated event wins when there is no override.
    assert select_cards(blueprint, told_events)[0]["kind"] == "time"
    # New: a detective-authored override for that beat wins instead.
    overridden = select_cards(blueprint, told_events,
                              card_overrides={"B01": {"kind": "contradiction", "source_fact": "C1"}})
    assert (overridden[0]["kind"], overridden[0]["source_fact"]) == ("contradiction", "C1")


def test_obstacle_3_a_detective_unknown_with_no_fact_row_passes_check_item(db_session):
    """engines.py:check_item rejects basis="unknown" unless the cited ref is category="question"
    or disputed. A DetectiveQuestion has no backing Fact/Q row at all - this proves it still
    passes, because `legacy_evidence_additions` re-shapes it into exactly that category."""
    from app.documentary.mvp.engines import Fact as RefFact
    from app.documentary.mvp.engines import check_item

    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "gun-1", "the 9mm", "object", "B01", 0)
    MEM.add_question(db_session, graph.id, "q-location", ["gun-1"], "Where is the gun?",
                     "not_yet_explained")
    facts, questions = D.legacy_evidence_additions(db_session, graph.id)
    assert questions == [{"id": "q-location", "question": "Where is the gun?"}]
    refs = {q["id"]: RefFact(q["id"], q["question"], True, "question") for q in questions}
    item = {"basis": "unknown", "fact_id": "q-location", "text": "the gun's location"}
    assert check_item(item, refs, "path") is None   # no "does not record an open question" rejection


def test_needs_py_seam_detective_requirements_are_authoritative_when_they_cover_a_beat(db_session):
    """Section A's obstacle: `plan_needs` used to independently invent every beat's requirement.
    This proves a detective-covered beat's requirement comes from the detective's own tool
    choice, not a fresh agent guess, while an uncovered beat still falls back to the agent
    (exercised indirectly: see the full ungated regression suite for the agent path unchanged)."""
    from app.documentary.detective.dispatch import detective_raw_needs
    from app.documentary.mvp.needs import validate_needs

    graph, blueprint_row, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "gun-1", "the 9mm", "object", "B01", 0)
    _scene_with_action(db_session, graph, "B01", 0, "timeline", purpose="show when the gun went missing",
                      active_entity="gun-1")
    raw = detective_raw_needs(db_session, graph)
    assert raw["entities"] == [{"key": "gun-1", "name": "the 9mm", "type": "object"}]
    beat = raw["beats"][0]
    assert beat["beat_id"] == "B01" and beat["requirements"][0]["function"] == "time"
    view = {"nodes": [{"key": "gun-1", "label": "the 9mm", "category": "object",
                       "first_beat": "B01", "first_order": 0}], "edges": [], "beat_order": {"B01": 0}}
    requirements, report = validate_needs(raw, json.loads(blueprint_row.blueprint_json), {"facts": []}, [], view)
    assert requirements["beats"][0]["requirements"][0]["entity"] == "gun_1"
    assert report["status"] in ("valid", "needs_review")
