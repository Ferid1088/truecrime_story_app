"""The Detective Director's real output binds an action to claims/questions as a flat list of ID strings
(director.py `content_bindings_format`). The dispatch readers used to accept only {"claim_key": ...} objects, so
no real action resolved its claims and Section F's release gating never fired on real output - found by the
longer real-passage run (C3). Both forms must resolve."""

from __future__ import annotations

import json
from uuid import uuid4

from app.db.models import (
    Case,
    DetectiveScene,
    DetectiveToolAction,
    EditorialBlueprint,
    RevealGraph,
    StoryVersion,
)
from app.documentary import memory as MEM
from app.documentary.detective import dispatch as D

UNIT = "Officers found the gun in the lake, but the owner was never named."


def _action(db_session, bindings):
    case = Case(canonical_title="Bindings test", slug=f"bindings-{uuid4().hex[:10]}", language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                         narrative_angle="t", story_text="p", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    bp = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1, status="valid",
                            blueprint_json=json.dumps({"beats": [{"id": "B01"}]}))
    db_session.add(bp)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=bp.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    unit = MEM.add_narration_unit(db_session, graph.id, unit_key="B01-1", beat_id="B01", story_order=0,
                                  text=UNIT, language="en")
    MEM.ensure_entity(db_session, graph.id, "gun", "the gun", "object", "B01", 0)
    anchor = MEM.add_release_anchor(db_session, graph.id, "a1", unit.id, UNIT.index("owner"), len(UNIT) - 1)
    MEM.add_claim(db_session, graph.id, "c1", "gun", "found", "The gun was found in the lake.",
                 "established_in_text", supporting_unit_keys=["B01-1"])
    MEM.add_claim(db_session, graph.id, "c2", "gun", "owner", "The owner was never named.",
                 "explicit_unknown", supporting_unit_keys=["B01-1"], release_anchor_id=anchor.id)
    MEM.add_question(db_session, graph.id, question_key="q1", entity_node_keys=["gun"], text="Who owned it?",
                     kind="explicit_unresolved", supporting_claim_keys=["B01-1"])
    scene = DetectiveScene(
        graph_id=graph.id, scene_key="scn1", scene_order=0, mode="direct_scenes",
        covered_unit_keys_json='["B01-1"]', aim="a", active_question_keys_json="[]",
        active_entity_keys_json="[]", change_in_understanding="c", alternatives_considered_json="[]",
        why_selected="w", expected_audience_gain="g", audio_clarity_findings_json="[]")
    db_session.add(scene)
    db_session.flush()
    action = DetectiveToolAction(
        scene_id=scene.id, action_key="act1", order_index=0, tool_id="fact_card", purpose="p",
        payload_json="{}", content_bindings_json=json.dumps(bindings), continuity_refs_json="[]")
    db_session.add(action)
    db_session.flush()
    return action


def test_flat_string_bindings_resolve_claims_by_their_own_kind(db_session):
    action = _action(db_session, ["c1", "c2", "q1"])
    assert D.binding_keys(action, "claim_key") == ["c1", "c2", "q1"]   # a key of another kind matches no row
    assert [c.claim_key for c in D._bound_claims(db_session, action)] == ["c1", "c2"]


def test_the_object_forms_still_resolve(db_session):
    action = _action(db_session, [{"claim_key": "c1"}, {"claim_id": "c2"}, {"id": "q1"}])
    assert [c.claim_key for c in D._bound_claims(db_session, action)] == ["c1", "c2"]


def test_a_fact_card_builds_from_the_directors_real_binding_format(db_session):
    action = _action(db_session, ["c1", "c2"])
    data = D._fact_card_data(db_session, action)
    assert [f["fact_id"] for f in data["facts"]] == ["c1", "c2"]


def test_release_gating_fires_on_the_directors_real_binding_format(db_session):
    """Section F: the late words ("the owner was never named") hold the card back - for real output too."""
    action = _action(db_session, ["c1", "c2"])
    fraction = D._min_release_fraction(db_session, action)
    assert fraction is not None and fraction > 0.5
