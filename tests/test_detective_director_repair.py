"""The Director's bounded repair: an output that breaks an invariant is sent back once with the exact issues
instead of sinking the whole call. Found by the real 84-sentence run on case 15's B13-B20 (C3): one claim listed
a claim ID among its entity IDs, persistence rejected it, and the otherwise sound whole-film map was lost.
Provider faked (no spend); real PostgreSQL."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.ai_config import ai_config
from app.db.models import AgentRun, DetectiveClaim, DetectiveScene, DetectiveToolAction, NarrationUnit
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


def _actions(db_session, graph):
    return (db_session.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
            .filter(DetectiveScene.graph_id == graph.id).count())


def _unbound():
    payload = GOOD()
    payload["scenes"][0]["actions"][0]["content_bindings"] = []
    return payload


def test_an_action_whose_card_cannot_be_built_is_repaired_and_only_the_buildable_output_is_kept(db_session, monkeypatch):
    graph = _setup(db_session)
    fake, result = _map(db_session, graph, monkeypatch, _unbound(), GOOD())

    assert len(fake.calls) == 2 and fake.calls[1]["mode"] == "repair"
    assert any("action a1 (fact_card) cannot be built" in issue for issue in fake.calls[1]["repair_target"]["issues"])
    assert _actions(db_session, graph) == 1
    from app.documentary.detective import buildable as BD

    assert BD.unbuildable(db_session, graph.id) == []
    assert BD.alternative_seconds(db_session, graph.id, "en") > 0


def test_a_card_that_stays_unbuildable_after_the_repairs_is_dropped_and_the_rest_is_kept(db_session, monkeypatch):
    graph = _setup(db_session)
    fake, result = _map(db_session, graph, monkeypatch, _unbound())
    assert len(fake.calls) == ai_config.detective_toolkit.max_repair_iterations + 1
    assert result["dropped_actions"] == ["a1"] and _actions(db_session, graph) == 0
    assert db_session.query(DetectiveClaim).filter_by(graph_id=graph.id).count() == 1, "the claims are kept"


def test_a_claim_without_an_entity_is_sent_back_for_repair_instead_of_crashing_the_persist(db_session, monkeypatch):
    graph = _setup(db_session)
    fake, result = _map(db_session, graph, monkeypatch, _payload([]), GOOD())
    assert len(fake.calls) == 2 and any("entity_ids is empty" in i for i in fake.calls[1]["repair_target"]["issues"])
    assert result["counts"]["claims"] == 1


def test_an_entity_without_an_id_is_sent_back_for_repair_instead_of_crashing_the_persist(db_session, monkeypatch):
    graph = _setup(db_session)
    bad = GOOD()
    bad["entities"].append({"type": "object", "name": "a nameless thing", "beat_id": "B01", "order": 2})
    fake, result = _map(db_session, graph, monkeypatch, bad, GOOD())
    assert len(fake.calls) == 2 and any("entity missing required field" in i for i in fake.calls[1]["repair_target"]["issues"])
    assert result["counts"]["claims"] == 1


def test_a_claim_that_names_an_unknown_entity_after_every_repair_is_dropped_and_the_rest_is_kept(db_session, monkeypatch):
    graph = _setup(db_session)
    bad = _payload(["gun"])
    bad["claims"].append({"id": "c-bad", "entity_ids": ["c-origin"], "attribute": "x", "text": "t",
                          "basis": "established_in_text", "supporting_span_ids": ["B01-1"]})
    fake, result = _map(db_session, graph, monkeypatch, bad)
    assert len(fake.calls) == ai_config.detective_toolkit.max_repair_iterations + 1
    keys = {c.claim_key for c in db_session.query(DetectiveClaim).filter_by(graph_id=graph.id)}
    assert keys == {"c-origin"} and "dropped_reason" in result


def test_a_scene_without_an_id_is_repaired_instead_of_crashing_the_persist(db_session, monkeypatch):
    graph = _setup(db_session)
    bad = GOOD()
    del bad["scenes"][0]["id"]
    fake, result = _map(db_session, graph, monkeypatch, bad, GOOD())
    assert len(fake.calls) == 2 and any("malformed output" in i for i in fake.calls[1]["repair_target"]["issues"])
    assert _actions(db_session, graph) == 1


def _mutations(payload):
    """Every single-field damage a model could do to a good output: a field removed, set to None, or given the
    wrong type, at every level (envelope, entity, claim, question, relation, event, scene, action)."""
    def paths(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                yield path + (key,)
                yield from paths(value, path + (key,))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield path + (index,)
                yield from paths(value, path + (index,))

    def with_change(change, path):
        copy = json.loads(json.dumps(payload))
        parent = copy
        for step in path[:-1]:
            parent = parent[step]
        if change == "remove":
            if isinstance(parent, dict):
                del parent[path[-1]]
            else:
                parent.pop(path[-1])
        else:
            parent[path[-1]] = {"none": None, "string": "x", "number": 7, "list": [], "dict": {}}[change]
        return copy

    for path in paths(payload):
        for change in ("remove", "none", "string", "number", "list", "dict"):
            yield path, change, with_change(change, path)


@pytest.mark.parametrize("repairs", [0, 2])
def test_no_single_field_damage_to_a_directors_output_crashes_the_call(db_session, monkeypatch, repairs):
    """The Director's output is untrusted: whatever is wrong with it, the call repairs it, keeps its valid part or
    raises DirectorValidationFailed - never an uncaught KeyError, TypeError or IndexError."""
    monkeypatch.setattr(ai_config.detective_toolkit, "max_repair_iterations", repairs)
    crashes = []
    for path, change, damaged in _mutations(GOOD()):
        graph = _setup(db_session)
        monkeypatch.setattr(director, "get_generation_provider", lambda d=damaged: SequenceGen(d))
        try:
            asyncio.run(director.map_film(db_session, graph.id, language="en", text_version=1))
        except director.DirectorValidationFailed:
            pass
        except Exception as e:                                           # noqa: BLE001 - this is what is hunted
            crashes.append((path, change, type(e).__name__, str(e)[:80]))
            db_session.rollback()
    import collections

    summary = collections.Counter((c[2], c[3][:50]) for c in crashes)
    assert crashes == [], f"{len(crashes)} crashes: {dict(summary)}"


def test_the_director_input_starts_with_what_is_the_same_in_every_call_so_a_prefix_cache_can_reuse_it(db_session, monkeypatch):
    graph = _setup(db_session)
    fake, _ = _map(db_session, graph, monkeypatch, BAD(), GOOD())
    first, repair = (list(c) for c in fake.calls)
    present = [k for k in director.STABLE_FIRST if k in first]
    assert first[:len(present)] == present
    assert first.index("narration") < first.index("mode") and first.index("narration") < first.index("existing_state")
    assert repair.index("narration") < repair.index("repair_target")
    assert fake.calls[0]["narration"] == fake.calls[1]["narration"]


def test_a_card_the_renderer_refuses_is_repaired_with_the_renderers_own_reason(db_session, monkeypatch):
    graph = _setup(db_session)
    many = GOOD()
    many["claims"] += [{"id": f"c{i}", "entity_ids": ["gun"], "attribute": "origin", "text": f"fact number {i}",
                        "basis": "established_in_text", "supporting_span_ids": ["B01-1"]} for i in range(4)]
    many["scenes"][0]["actions"][0]["content_bindings"] = ["c-origin", "c0", "c1", "c2"]
    fake, _ = _map(db_session, graph, monkeypatch, many, GOOD())
    issues = fake.calls[1]["repair_target"]["issues"]
    assert any("action a1 (fact_card) cannot be rendered" in i and "facts" in i for i in issues)


def test_the_toolkit_removes_the_actions_it_cannot_build_but_keeps_the_scene_so_the_beat_stays_directed(db_session, monkeypatch):
    from app.documentary.detective import buildable as BD
    from app.documentary.detective import toolkit as TK

    graph = _setup(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit, "require_buildable", False)
    fake = SequenceGen({**_unbound(), "mode": "direct_scenes"})
    monkeypatch.setattr(director, "get_generation_provider", lambda: fake)
    asyncio.run(director.direct_scenes(db_session, graph.id, scene_unit_keys=["B01-1"], language="en", text_version=1))
    assert _actions(db_session, graph) == 1 and len(BD.unbuildable(db_session, graph.id)) == 1
    assert BD.prune_unbuildable(db_session, graph.id) == 1
    assert _actions(db_session, graph) == 0 and db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() == 1
    units = db_session.query(NarrationUnit).filter_by(graph_id=graph.id).all()
    assert TK._directed_beats(db_session, graph.id, units) == {"B01"}, "the beat is not planned (and paid for) again"
    assert db_session.query(DetectiveClaim).filter_by(graph_id=graph.id).count() == 1, "the claims stay"
