"""The Director's bounded repair: an output that breaks an invariant is sent back once with the exact issues
instead of sinking the whole call. Found by the real 84-sentence run on case 15's B13-B20 (C3): one claim listed
a claim ID among its entity IDs, persistence rejected it, and the otherwise sound whole-film map was lost.
Provider faked (no spend); real PostgreSQL."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.ai_config import ai_config
from app.db.models import AgentRun, DetectiveClaim, DetectiveScene
from app.documentary.detective import director
from app.documentary.memory import add_narration_unit
from app.providers.generation.base import GenerationResult
from tests.test_detective_director import _entity, _graph


class SequenceGen:
    """Answers each call with the next payload (the last one repeats) and keeps every input it was sent."""

    def __init__(self, *payloads):
        self.payloads, self.calls = list(payloads), []

    async def generate_structured(self, role, system, user, images=None, **kw):
        self.calls.append(json.loads(user))
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        return (payload, GenerationResult(text="{}", model="fake", provider="fake"))


def _payload(entity_ids):
    return {
        "schema_version": "1.0", "run_id": "run-1", "film_id": "f", "language": "en", "text_version": 1,
        "mode": "map_film",
        "entities": [{"id": "gun", "type": "object", "name": "9mm handgun", "beat_id": "B01", "order": 1}],
        "claims": [{"id": "c-origin", "entity_ids": entity_ids, "attribute": "origin",
                    "text": "found inside the house", "basis": "established_in_text",
                    "supporting_span_ids": ["B01-1"]}],
        "questions": [], "relations": [],
        "knowledge_events": [{"subject_type": "entity", "subject_id": "gun", "operation": "introduce",
                              "new_state": {}},
                             {"subject_type": "claim", "subject_id": "c-origin", "operation": "introduce",
                              "new_state": {}}],
        "scenes": [{"id": "s1", "covered_span_ids": ["B01-1"], "aim": "introduce the gun",
                    "active_question_ids": [], "active_thread_ids": ["gun"], "change_in_understanding": "c",
                    "alternatives_considered": [], "why_selected": "w", "expected_audience_gain": "g",
                    "audio_clarity_findings": [],
                    "actions": [{"id": "a1", "tool_id": "fact_card", "purpose": "state it", "payload": {},
                                 "content_bindings": ["c-origin"]}]}],
        "requests": [], "affected_scene_ids": [], "checks": {},
    }


BAD = lambda: _payload(["clm_not_an_entity"])      # noqa: E731 - a claim ID where an entity ID belongs
GOOD = lambda: _payload(["gun"])                     # noqa: E731


def _setup(db_session):
    graph = _graph(db_session, beats=[{"id": "B01"}])
    _entity(db_session, graph, "gun", "9mm handgun", "B01", 1)
    add_narration_unit(db_session, graph.id, "B01-1", "B01", 1, "en", "Detectives found a 9mm handgun inside the house.")
    db_session.commit()
    return graph


def _map(db_session, graph, monkeypatch, *payloads):
    fake = SequenceGen(*payloads)
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    return fake, asyncio.run(director.map_film(db_session, graph.id, language="en", text_version=1))


def test_a_bad_reference_is_repaired_once_and_only_the_repaired_output_is_persisted(db_session, monkeypatch):
    graph = _setup(db_session)
    fake, result = _map(db_session, graph, monkeypatch, BAD(), GOOD())

    assert len(fake.calls) == 2
    retry = fake.calls[1]
    assert retry["mode"] == "repair"
    assert retry["repair_target"]["previous_output"]["claims"][0]["entity_ids"] == ["clm_not_an_entity"]
    assert any("clm_not_an_entity" in issue for issue in retry["repair_target"]["issues"])
    assert result["counts"]["claims"] == 1
    claim = db_session.query(DetectiveClaim).filter_by(graph_id=graph.id, claim_key="c-origin").one()
    assert claim.entity_node_key == "gun"


def test_every_attempt_is_its_own_tracked_run(db_session, monkeypatch):
    graph = _setup(db_session)
    _map(db_session, graph, monkeypatch, BAD(), GOOD())
    names = [r.name if hasattr(r, "name") else r.agent_name for r in
             db_session.query(AgentRun).filter(AgentRun.case_id == graph.case_id).order_by(AgentRun.id)]
    assert any("map_film (repair 1)" in n for n in names) and any(n.endswith("map_film") for n in names)


def test_a_call_that_never_passes_raises_after_the_bound_and_persists_nothing(db_session, monkeypatch):
    graph = _setup(db_session)
    fake = SequenceGen(BAD())
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    with pytest.raises(director.DirectorValidationFailed):
        asyncio.run(director.map_film(db_session, graph.id, language="en", text_version=1))
    assert len(fake.calls) == ai_config.detective_toolkit.max_repair_iterations + 1
    assert db_session.query(DetectiveClaim).filter_by(graph_id=graph.id).count() == 0
    assert db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() == 0


def test_no_repair_when_the_bound_is_zero(db_session, monkeypatch):
    graph = _setup(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_repair_iterations", 0)
    fake = SequenceGen(BAD(), GOOD())
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    with pytest.raises(director.DirectorValidationFailed):
        asyncio.run(director.map_film(db_session, graph.id, language="en", text_version=1))
    assert len(fake.calls) == 1


def test_a_clean_first_answer_costs_exactly_one_call(db_session, monkeypatch):
    graph = _setup(db_session)
    fake, _ = _map(db_session, graph, monkeypatch, GOOD())
    assert len(fake.calls) == 1 and fake.calls[0]["mode"] == "map_film"
