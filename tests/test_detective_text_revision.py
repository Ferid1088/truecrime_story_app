"""Section F, checklist row F3 - the narration must stand on its own, and what it lacks goes to the writing
workflow as a precise request. Real PostgreSQL (`db_session`); no model call: the Director's output is a canned
payload run through the real validator and persistence."""

from __future__ import annotations

import json
from uuid import uuid4

from app.db.models import (
    Case,
    DetectiveFinding,
    DetectiveScene,
    DetectiveToolAction,
    EditorialBlueprint,
    NarrationUnit,
    ReleaseAnchor,
    RevealGraph,
    StoryVersion,
)
from app.documentary import memory as MEM
from app.documentary.detective import text_revision as TR
from app.documentary.detective.persist import persist_director_output
from app.documentary.detective.schema import NarrationIndex, validate_director_output
from app.language_pipeline.review import critique_from

UNIT = "A man was seen near the house that night, and Caleb said he never opened the garage."


def _graph(db_session):
    case = Case(canonical_title="Text revision test", slug=f"text-revision-{uuid4().hex[:10]}", language="en")
    db_session.add(case)
    db_session.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                         narrative_angle="test", story_text="placeholder", engagement_score=0.0)
    db_session.add(story)
    db_session.flush()
    blueprint = EditorialBlueprint(case_id=case.id, story_version_id=story.id, version=1, status="valid",
                                   blueprint_json=json.dumps({"beats": [{"id": "B01"}]}))
    db_session.add(blueprint)
    db_session.flush()
    graph = RevealGraph(case_id=case.id, blueprint_id=blueprint.id, version=1, status="validated")
    db_session.add(graph)
    db_session.flush()
    MEM.add_narration_unit(db_session, graph.id, unit_key="B01-1", beat_id="B01", story_order=0,
                           text=UNIT, language="en")
    MEM.ensure_entity(db_session, graph.id, "gun", "the gun", "object", "B01", 0)
    return graph


def _narration_index(db_session, graph):
    units = [{"unit_key": u.unit_key, "story_order": u.story_order, "text": u.text}
             for u in db_session.query(NarrationUnit).filter_by(graph_id=graph.id)]
    return NarrationIndex.build(units, [])


def _request(**override):
    base = {"id": "r1", "type": "text_revision", "scene_id": "scn1", "unit_key": "B01-1",
            "passage": "Caleb said he never opened the garage",
            "problem": "The board shows the garage was found open, which the narration never says.",
            "proposed_repair": "Add: 'The garage door was found standing open.'"}
    return {**base, **override}


def _payload(*requests):
    return {"schema_version": "1.0", "run_id": "r", "film_id": "f", "language": "en", "text_version": 1,
            "mode": "direct_scenes", "requests": list(requests)}


def test_the_directors_text_revision_request_is_persisted_against_the_exact_passage(db_session):
    graph = _graph(db_session)
    payload = _payload(_request())
    assert validate_director_output(payload, _narration_index(db_session, graph), set()) == []
    counts = persist_director_output(db_session, graph.id, None, "direct_scenes", payload)
    assert counts["text_revision_requests"] == 1

    row = db_session.query(DetectiveFinding).filter_by(graph_id=graph.id, finding_key="req:r1").one()
    assert (row.repair_owner, row.mode, row.status) == ("narration_workflow", "director_request", "open")
    assert row.scene_key == "scn1" and "garage was found open" in row.observed_problem
    assert row.recommended_repair.startswith("Add:")
    anchor = db_session.query(ReleaseAnchor).filter_by(graph_id=graph.id, anchor_key=row.anchor_key).one()
    assert UNIT[anchor.char_start:anchor.char_end] == "Caleb said he never opened the garage"

    persist_director_output(db_session, graph.id, None, "direct_scenes", payload)
    assert db_session.query(DetectiveFinding).filter_by(graph_id=graph.id).count() == 1, "idempotent"


def test_a_request_must_point_at_the_exact_words_of_a_real_unit(db_session):
    graph = _graph(db_session)
    index = _narration_index(db_session, graph)
    wrong_words = validate_director_output(_payload(_request(passage="words that are not there")), index, set())
    assert any("not the exact words" in v for v in wrong_words)
    wrong_unit = validate_director_output(_payload(_request(unit_key="B09-1")), index, set())
    assert any("unknown narration unit" in v for v in wrong_unit)
    incomplete = validate_director_output(_payload(_request(proposed_repair="")), index, set())
    assert any("missing field" in v for v in incomplete)


def test_requests_that_are_not_text_revisions_create_no_narration_finding(db_session):
    graph = _graph(db_session)
    payload = _payload({"id": "r2", "type": "asset", "problem": "needs a photo of the garage"})
    assert validate_director_output(payload, _narration_index(db_session, graph), set()) == []
    counts = persist_director_output(db_session, graph.id, None, "direct_scenes", payload)
    assert counts["text_revision_requests"] == 0
    assert db_session.query(DetectiveFinding).filter_by(graph_id=graph.id).count() == 0


def test_open_requests_become_the_revision_items_the_language_pipeline_consumes(db_session):
    graph = _graph(db_session)
    persist_director_output(db_session, graph.id, None, "direct_scenes", _payload(_request()))
    items = TR.revision_items(db_session, graph.id)
    assert len(items) == 1
    assert items[0]["quote"] == "Caleb said he never opened the garage" and items[0]["act"] == "B01"
    assert items[0]["severity"] == "medium" and items[0]["instruction"].startswith("Add:")
    critique = critique_from(items, [])
    assert [i["quote"] for i in critique["items"]] == ["Caleb said he never opened the garage"]

    row = db_session.query(DetectiveFinding).filter_by(graph_id=graph.id).one()
    row.status = "closed"
    db_session.flush()
    assert TR.revision_items(db_session, graph.id) == [], "a closed request is not sent to the writers again"


def _scene(db_session, graph, claim_key, *, binding=None, covered=("B01-1",), question=None):
    scene = DetectiveScene(
        graph_id=graph.id, scene_key="scn1", scene_order=0, mode="direct_scenes",
        covered_unit_keys_json=json.dumps(list(covered)), aim="aim", active_question_keys_json="[]",
        active_entity_keys_json="[]", change_in_understanding="c", alternatives_considered_json="[]",
        why_selected="w", expected_audience_gain="g", audio_clarity_findings_json="[]")
    db_session.add(scene)
    db_session.flush()
    bindings = binding if binding is not None else [claim_key] + ([question] if question else [])
    db_session.add(DetectiveToolAction(
        scene_id=scene.id, action_key="act1", order_index=0, tool_id="fact_card", purpose="p",
        payload_json="{}", content_bindings_json=json.dumps(bindings), continuity_refs_json="[]",
        planner_decision="accepted"))
    db_session.flush()


def test_a_claim_the_narration_does_not_say_is_flagged_and_requested(db_session):
    graph = _graph(db_session)
    MEM.add_claim(db_session, graph.id, "c-lake", "gun", "location", "The gun was recovered from the lake.",
                 "established_in_text", supporting_unit_keys=["B01-1"])
    _scene(db_session, graph, "c-lake")
    findings = TR.audio_only_findings(db_session, graph.id)
    assert [f["subject"] for f in findings] == ["c-lake"]
    assert findings[0]["unit_key"] == "B01-1" and "recovered from the lake" in findings[0]["problem"]

    rows = TR.request_audio_only_revisions(db_session, graph.id)
    assert [r.mode for r in rows] == ["audio_only_audit"] and rows[0].repair_owner == "narration_workflow"
    assert len(TR.request_audio_only_revisions(db_session, graph.id)) == 1
    assert db_session.query(DetectiveFinding).filter_by(graph_id=graph.id).count() == 1, "idempotent"
    assert TR.revision_items(db_session, graph.id)[0]["quote"] == UNIT, "the whole unit is the passage"


def test_a_claim_the_narration_says_is_not_flagged(db_session):
    graph = _graph(db_session)
    MEM.add_claim(db_session, graph.id, "c-said", "gun", "who", "A man was seen near the house that night.",
                 "established_in_text", supporting_unit_keys=["B01-1"])
    _scene(db_session, graph, "c-said")
    assert TR.audio_only_findings(db_session, graph.id) == []


def test_a_claim_with_no_supporting_span_is_something_only_the_screen_says(db_session):
    graph = _graph(db_session)
    MEM.add_claim(db_session, graph.id, "c-bare", "gun", "owner", "The gun belonged to the victim.",
                 "established_in_text")
    _scene(db_session, graph, "c-bare")
    findings = TR.audio_only_findings(db_session, graph.id)
    assert len(findings) == 1 and "only the visual says it" in findings[0]["problem"]


def test_a_questions_premise_must_come_from_the_narration(db_session):
    graph = _graph(db_session)
    MEM.add_question(db_session, graph.id, question_key="q-ok", entity_node_keys=["gun"],
                     text="Where is the gun?", kind="not_yet_explained", supporting_claim_keys=["B01-1"])
    MEM.add_question(db_session, graph.id, question_key="q-orphan", entity_node_keys=["gun"],
                     text="Who owned it?", kind="not_yet_explained", supporting_claim_keys=["nothing"])
    _scene(db_session, graph, "", binding=["q-ok", "q-orphan"])
    findings = TR.audio_only_findings(db_session, graph.id)
    assert [f["subject"] for f in findings] == ["question:q-orphan"]


def test_a_reviewer_finding_about_a_narration_unit_gets_an_exact_anchor_the_writers_can_quote(db_session):
    """The live reviewer names the passage by unit (`B01-1`); that becomes a real anchor over the unit's words."""
    from app.documentary.detective.review import persist_findings

    graph = _graph(db_session)
    raw = {"mode": "plan_audit", "findings": [
        {"id": "fnd-pronoun", "severity": "major", "repair_owner": "narration_workflow",
         "observed_problem": "'he' has no antecedent", "anchor": "B01-1", "recommended_repair": "Name him."},
        {"id": "fnd-design", "severity": "minor", "repair_owner": "visual_design_director",
         "observed_problem": "too dense", "anchor": "not-a-unit"}]}
    narration, design = persist_findings(db_session, graph.id, raw)
    assert narration.anchor_key == "rev:fnd-pronoun" and design.anchor_key == "not-a-unit"
    item = TR.revision_items(db_session, graph.id)[0]
    assert item["quote"] == UNIT and item["act"] == "B01" and item["instruction"] == "Name him."
