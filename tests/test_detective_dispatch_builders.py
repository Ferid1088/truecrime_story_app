"""Section D2 — dispatch builders for the tools beyond fact_card/floor_plan/timeline. Each test
proves a builder produces data that the REAL renderer (Graphic.can_render + draw_state, or an
engine's equivalent) actually accepts and draws, not just a plausible-looking dict."""

from __future__ import annotations

import json

from app.documentary.detective import dispatch as D
from app.documentary import memory as MEM
from app.documentary.graphics.registry import GRAPHICS
from app.documentary.graphics.engines.evidence import ChainOfCustody, MapOfUnknowns
from app.documentary.graphics.engines.trial import EvidenceCheck

from tests.test_detective_section_d import _graph, _scene_with_action


def _bind(action, db_session, **keys):
    action.content_bindings_json = json.dumps([{k: v} for k, v in keys.items()])
    db_session.flush()


def test_detective_board_builder_renders_T02(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "role", "Jane was home alone.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "detective_board")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    data = D._detective_board_data(db_session, action)
    assert data == {"cards": [{"id": "c1", "title": "Jane was home alone.", "fact_id": "c1"}]}


def test_recap_builder_renders_T03(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "role", "Jane was home.", "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "p1", "role", "Ages disputed.", "disputed")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "recap")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._recap_data(db_session, action)
    assert GRAPHICS["recap"].can_render(data) is None
    GRAPHICS["recap"].draw_state(data, "en", 1)


def test_unknowns_builder_renders_T04(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "role", "Jane was home.", "established_in_text")
    MEM.add_question(db_session, graph.id, "q1", ["p1"], "Who else was there?", "not_yet_explained")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "unknowns")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"question_key": "q1"}])
    db_session.flush()
    data = D._unknowns_data(db_session, action)
    assert MapOfUnknowns().can_render(data) is None
    MapOfUnknowns().draw(json.dumps(data), "en", 2)


def test_conflict_builder_renders_T05(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "src-a", "The Guardian", "organization", "B01", 0)
    MEM.ensure_entity(db_session, graph.id, "src-b", "NBC News", "organization", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "src-a", "age", "The Guardian said nine and eleven.",
                 "attributed", attribution="The Guardian")
    MEM.add_claim(db_session, graph.id, "c2", "src-b", "age", "NBC said nine and twelve.",
                 "attributed", attribution="NBC News")
    MEM.add_relation(db_session, graph.id, "r1", "src-a", "src-b", "conflict", "Daughter's age disputed")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "conflict")
    action.content_bindings_json = json.dumps(
        [{"relation_key": "r1"}, {"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._conflict_data(db_session, action)
    assert GRAPHICS["conflict"].can_render(data) is None
    GRAPHICS["conflict"].draw_state(data, "en", 1)


def test_conflict_board_builder_renders_T06(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "a", "A", "organization", "B01", 0)
    MEM.ensure_entity(db_session, graph.id, "b", "B", "organization", "B01", 0)
    MEM.ensure_entity(db_session, graph.id, "c", "C", "organization", "B01", 0)
    MEM.add_relation(db_session, graph.id, "r1", "a", "b", "conflict", "First disagreement")
    MEM.add_relation(db_session, graph.id, "r2", "b", "c", "conflict", "Second disagreement")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "conflict_board")
    action.content_bindings_json = json.dumps([{"relation_key": "r1"}, {"relation_key": "r2"}])
    db_session.flush()
    data = D._conflict_board_data(db_session, action)
    assert GRAPHICS["conflict_board"].can_render(data) is None
    GRAPHICS["conflict_board"].draw_state(data, "en", 1)


def test_key_facts_builder_renders_T10(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Jane Doe", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "age", "32 is Jane's age.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "key_facts")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    data = D._key_facts_data(db_session, action)
    assert GRAPHICS["key_facts"].can_render(data) is None
    GRAPHICS["key_facts"].draw_state(data, "en", 1)


def test_quote_builder_renders_T12(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "p1", "Caleb Flynn", "person", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "p1", "statement", "Somebody broke into my home.",
                 "attributed", attribution="Caleb Flynn")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "quote")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}])
    db_session.flush()
    data = D._quote_data(db_session, action)
    assert GRAPHICS["quote"].can_render(data) is None
    GRAPHICS["quote"].draw_state(data, "en", 1)


def test_evidence_check_builder_renders_T15(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "item", "Spent casings", "object", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "item", "found", "Two casings were found at the scene.",
                 "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "item", "owner", "Who fired them is unknown.", "explicit_unknown")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "evidence_check")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._evidence_check_data(db_session, action)
    assert EvidenceCheck().can_render(data) is None
    EvidenceCheck().draw(json.dumps(data), "en", 1)


def test_custody_builder_renders_T16(db_session):
    graph, _, _ = _graph(db_session, beats=[{"id": "B01"}])
    MEM.ensure_entity(db_session, graph.id, "item", "Spent casings", "object", "B01", 0)
    MEM.add_claim(db_session, graph.id, "c1", "item", "found", "Found near the bed.", "established_in_text")
    MEM.add_claim(db_session, graph.id, "c2", "item", "logged", "Logged into evidence.", "established_in_text")
    _, action = _scene_with_action(db_session, graph, "B01", 0, "custody")
    action.content_bindings_json = json.dumps([{"claim_key": "c1"}, {"claim_key": "c2"}])
    db_session.flush()
    data = D._custody_data(db_session, action)
    assert ChainOfCustody().can_render(data) is None
    ChainOfCustody().draw(json.dumps(data), "en", 1)
