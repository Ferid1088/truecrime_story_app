"""Section B — text-based investigation memory. Live PostgreSQL (db_session), no fakes/mocks:
every write below is a real row in the real database, per this project's verification rule."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.db.models import (
    Case,
    DetectiveKnowledgeEvent,
    EditorialBlueprint,
    RevealGraph,
    RevealNode,
    StoryVersion,
)
from app.documentary.memory import (
    MemoryValidationError,
    add_claim,
    add_narration_unit,
    add_question,
    add_relation,
    add_release_anchor,
    get_narration_span,
    record_knowledge_event,
    replay_state,
)


def _graph(db_session, beats=None):
    """A minimal, uniquely-keyed Case -> StoryVersion -> EditorialBlueprint -> RevealGraph chain,
    with zero Fact/Contradiction rows — proving the memory layer needs none."""
    slug = f"detective-memory-{uuid4().hex[:10]}"
    case = Case(canonical_title="Detective memory test", slug=slug, language="en")
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
    node = RevealNode(
        graph_id=graph.id, node_key=node_key, label=label, category=category,
        first_revealed_beat_id=beat_id, first_revealed_order=order,
    )
    db_session.add(node)
    db_session.flush()
    return node


def test_gun_example_partial_knowledge_then_later_answer(db_session):
    """The doc's required example: known type/origin, unresolved location, then a later passage
    answers it — same entity, no silent upgrade of wording, update recorded at its own anchor."""
    graph = _graph(db_session, beats=[{"id": "B01"}, {"id": "B05"}])
    _entity(db_session, graph, "gun_1", "The 9mm handgun", "B01", 0)

    unit_a = add_narration_unit(
        db_session, graph.id, "B01-S1", "B01", 0, "en",
        "Detectives found a 9mm handgun from the house, but its whereabouts today are unknown.",
    )
    anchor_a = add_release_anchor(db_session, graph.id, "B01-S1-A1", unit_a.id,
                                   char_start=0, char_end=len(unit_a.text))

    record_knowledge_event(db_session, graph.id, "entity", "gun_1", "introduce",
                            new_state={"label": "The 9mm handgun"}, anchor_id=anchor_a.id)

    claim_type = add_claim(db_session, graph.id, "gun_1-type", "gun_1", "type",
                            "a 9mm handgun", basis="established_in_text",
                            supporting_unit_keys=["B01-S1"], release_anchor_id=anchor_a.id)
    claim_origin = add_claim(db_session, graph.id, "gun_1-origin", "gun_1", "origin",
                              "from the house", basis="established_in_text",
                              supporting_unit_keys=["B01-S1"], release_anchor_id=anchor_a.id)
    record_knowledge_event(db_session, graph.id, "claim", claim_type.claim_key, "introduce",
                            new_state={"text": claim_type.text}, anchor_id=anchor_a.id)
    record_knowledge_event(db_session, graph.id, "claim", claim_origin.claim_key, "introduce",
                            new_state={"text": claim_origin.text}, anchor_id=anchor_a.id)

    claim_location_unknown = add_claim(
        db_session, graph.id, "gun_1-location-unk", "gun_1", "location",
        "its whereabouts today are unknown", basis="explicit_unknown",
        supporting_unit_keys=["B01-S1"], release_anchor_id=anchor_a.id,
    )
    record_knowledge_event(db_session, graph.id, "claim", claim_location_unknown.claim_key,
                            "introduce", new_state={"text": claim_location_unknown.text},
                            anchor_id=anchor_a.id)

    question = add_question(db_session, graph.id, "gun_1-where", ["gun_1"], "Where is it?",
                             kind="explicit_unresolved", opening_anchor_id=anchor_a.id,
                             supporting_claim_keys=[claim_location_unknown.claim_key])
    record_knowledge_event(db_session, graph.id, "question", question.question_key, "introduce",
                            new_state={"state": "open"}, anchor_id=anchor_a.id)

    # Nothing has answered it yet: the location claim is still "unknown", gun_1 is unchanged.
    mid_state = replay_state(db_session, graph.id)
    assert mid_state["questions"][question.question_key]["state"] == {"state": "open"}

    # A later passage answers the question. This must NOT rewrite the earlier claim's wording —
    # "from the house" never silently becomes "recovered at the house".
    unit_b = add_narration_unit(
        db_session, graph.id, "B05-S1", "B05", 4, "en",
        "Weeks later, the gun turned up in a storm drain two blocks from the house.",
    )
    anchor_b = add_release_anchor(db_session, graph.id, "B05-S1-A1", unit_b.id,
                                   char_start=0, char_end=len(unit_b.text))
    claim_location_found = add_claim(
        db_session, graph.id, "gun_1-location-found", "gun_1", "location",
        "found in a storm drain two blocks from the house", basis="established_in_text",
        supporting_unit_keys=["B05-S1"], release_anchor_id=anchor_b.id,
    )
    record_knowledge_event(db_session, graph.id, "claim", claim_location_found.claim_key,
                            "introduce", new_state={"text": claim_location_found.text},
                            anchor_id=anchor_b.id)
    record_knowledge_event(
        db_session, graph.id, "question", question.question_key, "resolve",
        new_state={"state": "answered"}, anchor_id=anchor_b.id,
        prior_state={"state": "open"},
        supporting_claim_keys=[claim_location_found.claim_key],
    )

    final_state = replay_state(db_session, graph.id)
    assert final_state["questions"][question.question_key]["state"] == {"state": "answered"}
    # The original claim text is untouched — no silent rewrite.
    assert claim_origin.text == "from the house"
    assert claim_location_unknown.text == "its whereabouts today are unknown"
    # Same entity throughout — no second "gun" node was ever created.
    assert db_session.query(RevealNode).filter_by(graph_id=graph.id, node_key="gun_1").count() == 1


def test_claim_question_relation_need_no_fact_or_contradiction_row(db_session):
    """Pure text-span grounding: zero Fact/Contradiction/Q rows exist anywhere in this test, and
    the claim/question/relation still validate and persist."""
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _entity(db_session, graph, "witness_1", "A neighbour", "B01", 0, category="person")
    _entity(db_session, graph, "suspect_1", "The suspect", "B01", 1, category="person")
    unit = add_narration_unit(db_session, graph.id, "B01-S1", "B01", 0, "en",
                               "A neighbour says she saw the suspect leave just after midnight.")
    anchor = add_release_anchor(db_session, graph.id, "B01-S1-A1", unit.id, 0, len(unit.text))

    claim = add_claim(db_session, graph.id, "witness_1-sighting", "witness_1", "statement",
                       "saw the suspect leave just after midnight", basis="attributed",
                       attribution="A neighbour", supporting_unit_keys=["B01-S1"],
                       release_anchor_id=anchor.id, evidence_fact_ids=None)
    question = add_question(db_session, graph.id, "suspect_1-alibi", ["suspect_1"],
                             "Does this match the suspect's account?", kind="interpretive_question",
                             opening_anchor_id=anchor.id, supporting_claim_keys=[claim.claim_key])
    relation = add_relation(db_session, graph.id, "witness-to-suspect", "witness_1", "suspect_1",
                             relation_type="attributed_statement",
                             label="neighbour's account of the suspect's movements",
                             supporting_unit_keys=["B01-S1"], release_anchor_id=anchor.id)

    assert claim.id is not None and json.loads(claim.evidence_fact_ids_json) == []
    assert question.id is not None
    assert relation.id is not None


def test_truncated_fixture_still_returns_complete_span(db_session):
    """A passage longer than the old 900-char / ~28-reference caps (engines.py:beat_view) must
    come back whole — the decisive clause at the end must not be dropped."""
    graph = _graph(db_session, beats=[{"id": "B01"}])
    long_text = ("The investigation proceeded through many uneventful weeks. " * 20
                 + "Finally, the decisive clause reveals the real culprit's name.")
    assert len(long_text) > 900
    add_narration_unit(db_session, graph.id, "B01-LONG", "B01", 0, "en", long_text)

    spans = get_narration_span(db_session, graph.id, beat_id="B01")
    assert len(spans) == 1
    assert spans[0]["text"] == long_text
    assert "the real culprit's name" in spans[0]["text"]


def test_replay_hides_future_answer_before_its_event(db_session):
    """Replaying only up to the order_seq before the answering event must show the question still
    open — a later answer must never leak into an earlier reconstruction."""
    graph = _graph(db_session, beats=[{"id": "B01"}, {"id": "B02"}])
    _entity(db_session, graph, "obj_1", "A locked box", "B01", 0)
    unit = add_narration_unit(db_session, graph.id, "B01-S1", "B01", 0, "en",
                               "A locked box was found, but no one knew what was inside.")
    anchor = add_release_anchor(db_session, graph.id, "B01-S1-A1", unit.id, 0, len(unit.text))
    question = add_question(db_session, graph.id, "obj_1-contents", ["obj_1"],
                             "What is inside the box?", kind="not_yet_explained",
                             opening_anchor_id=anchor.id)
    ev_open = record_knowledge_event(db_session, graph.id, "question", question.question_key,
                                      "introduce", new_state={"state": "open"}, anchor_id=anchor.id)
    record_knowledge_event(db_session, graph.id, "question", question.question_key, "resolve",
                            new_state={"state": "answered"}, anchor_id=anchor.id,
                            prior_state={"state": "open"})

    before = replay_state(db_session, graph.id, up_to_order_seq=ev_open.order_seq)
    after = replay_state(db_session, graph.id)
    assert before["questions"][question.question_key]["state"] == {"state": "open"}
    assert after["questions"][question.question_key]["state"] == {"state": "answered"}


def test_first_introduction_is_unique_but_reference_is_not(db_session):
    """Re-referencing an existing claim/question must never create a second first-reveal event."""
    graph = _graph(db_session, beats=[{"id": "B01"}, {"id": "B03"}])
    _entity(db_session, graph, "doc_1", "A letter", "B01", 0, category="document")
    unit = add_narration_unit(db_session, graph.id, "B01-S1", "B01", 0, "en",
                               "A letter was found among the victim's belongings.")
    anchor = add_release_anchor(db_session, graph.id, "B01-S1-A1", unit.id, 0, len(unit.text))

    record_knowledge_event(db_session, graph.id, "entity", "doc_1", "introduce",
                            new_state={"label": "A letter"}, anchor_id=anchor.id)
    with pytest.raises(MemoryValidationError):
        record_knowledge_event(db_session, graph.id, "entity", "doc_1", "introduce",
                                new_state={"label": "A letter"}, anchor_id=anchor.id)

    # A later, legitimate return to the same entity (e.g. its contents are discussed again) is
    # allowed under any non-"introduce" operation.
    unit_b = add_narration_unit(db_session, graph.id, "B03-S1", "B03", 2, "en",
                                 "The letter mentioned a debt no one had suspected.")
    anchor_b = add_release_anchor(db_session, graph.id, "B03-S1-A1", unit_b.id, 0, len(unit_b.text))
    reference_event = record_knowledge_event(db_session, graph.id, "entity", "doc_1", "reference",
                                              new_state={"note": "contents revisited"},
                                              anchor_id=anchor_b.id)
    assert reference_event.id is not None

    introduce_count = db_session.query(DetectiveKnowledgeEvent).filter_by(
        graph_id=graph.id, subject_type="entity", subject_key="doc_1", operation="introduce",
    ).count()
    assert introduce_count == 1
