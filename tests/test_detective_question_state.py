"""A question's state in real Director output (found by the real B13-B20 run, C3): the model returned the bare
status word as `new_state` ("opened") rather than an object, and a word outside the vocabulary. The memory and
board code read `{"status": ...}`, so such an event was stored unreadable and Section E's board status would crash.
Provider faked; real PostgreSQL."""

from __future__ import annotations

import asyncio

import pytest

from app.documentary import memory as MEM
from app.documentary.detective import director
from app.documentary.detective.board_state import question_status_as_of
from app.documentary.detective.persist import persist_director_output
from tests.test_detective_director import _entity, _graph
from tests.test_detective_director_repair import GOOD, SequenceGen, _setup


def _question_payload(statuses):
    """A map_film output whose question gets one event per given new_state value."""
    payload = GOOD()
    payload["questions"] = [{"id": "q1", "entity_ids": ["gun"], "question_text": "Where is it?",
                             "kind": "not_yet_explained", "supporting_span_ids": ["B01-1"]}]
    payload["knowledge_events"] += [{"subject_type": "question", "subject_id": "q1", "operation": op,
                                     "new_state": state} for op, state in statuses]
    return payload


def test_a_status_word_is_shaped_into_the_state_object(db_session):
    graph = _setup(db_session)
    persist_director_output(db_session, graph.id, None, "map_film",
                            _question_payload([("introduce", "open"), ("resolve", "answered")]))
    state = MEM.replay_state(db_session, graph.id)["questions"]["q1"]
    assert state["state"] == {"status": "answered"} and state["last_operation"] == "resolve"


def test_the_boards_question_status_reads_a_state_that_arrived_as_a_bare_word(db_session):
    graph = _setup(db_session)
    persist_director_output(db_session, graph.id, None, "map_film",
                            _question_payload([("introduce", "open"), ("update", "partial")]))
    assert question_status_as_of(db_session, graph.id, "q1", story_order=10) == "partial"


def test_a_status_outside_the_vocabulary_is_refused(db_session):
    graph = _setup(db_session)
    with pytest.raises(MEM.MemoryValidationError, match="not one of"):
        persist_director_output(db_session, graph.id, None, "map_film",
                                _question_payload([("introduce", "opened")]))


def test_a_state_that_is_neither_an_object_nor_a_word_is_refused(db_session):
    graph = _setup(db_session)
    with pytest.raises(MEM.MemoryValidationError, match="object or a status word"):
        persist_director_output(db_session, graph.id, None, "map_film", _question_payload([("introduce", 7)]))


def test_a_wrong_status_word_is_sent_back_and_the_repaired_event_is_what_persists(db_session, monkeypatch):
    graph = _setup(db_session)
    fake = SequenceGen(_question_payload([("introduce", "opened")]),
                       _question_payload([("introduce", "open"), ("resolve", "answered")]))
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    asyncio.run(director.map_film(db_session, graph.id, language="en", text_version=1))
    assert len(fake.calls) == 2 and any("opened" in i for i in fake.calls[1]["repair_target"]["issues"])
    assert MEM.replay_state(db_session, graph.id)["questions"]["q1"]["state"] == {"status": "answered"}


def test_the_model_is_told_the_exact_status_words_and_when_to_record_a_payoff(db_session, monkeypatch):
    graph = _setup(db_session)
    fake = SequenceGen(GOOD())
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    asyncio.run(director.map_film(db_session, graph.id, language="en", text_version=1))
    hints = fake.calls[0]["schema_hints"]
    assert hints["question_status_values"] == sorted(MEM.QUESTION_STATUSES)
    assert "answers it" in hints["question_state_rule"] and "opened" in hints["question_state_rule"]


def _two_actions(second_reason):
    payload = GOOD()
    payload["scenes"][0]["actions"].append({"id": "a2", "tool_id": "detective_board", "purpose": "revisit",
                                            "payload": {}, "content_bindings": ["c-origin"],
                                            "reuse_reason": second_reason})
    return payload


def test_a_return_without_a_reason_is_rejected_and_one_with_a_reason_passes():
    from app.documentary.detective.schema import NarrationIndex, validate_director_output

    index = NarrationIndex.build([{"unit_key": "B01-1", "story_order": 1}], [])
    tools = {"fact_card", "detective_board"}
    flagged = validate_director_output(_two_actions(None), index, tools)
    assert any("again" in v and "reuse_reason" in v and "first shown by action a1" in v for v in flagged)
    assert validate_director_output(_two_actions("the answer changes what the claim means"), index, tools) == []
    assert validate_director_output(GOOD(), index, tools) == [], "a first showing needs no reason"


def test_the_model_is_told_the_reuse_rule(db_session, monkeypatch):
    graph = _setup(db_session)
    fake = SequenceGen(GOOD())
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    asyncio.run(director.map_film(db_session, graph.id, language="en", text_version=1))
    assert "reuse_reason" in fake.calls[0]["schema_hints"]["reuse_reason_rule"]
