"""Cards the toolkit makes from the graph's stored data, with no model call (timeline, unknowns, conflict), the fill
cards it makes again after them, and the other changes that make the toolkit's run faster: the Director's reasoning
limit, the binding rules in its tool catalog, and one memory call per chunk. Real PostgreSQL via `db_session`."""

from __future__ import annotations

import asyncio
import json

from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, DetectiveToolAction, RevealNode
from app.documentary import memory as MEM
from app.documentary.detective import dispatch as D
from app.documentary.detective import filler as FL
from app.documentary.detective import graph_cards as GC
from app.documentary.detective import toolkit
from app.documentary.detective.narration import ingest_narration
from app.documentary.detective.tool_catalog import build_tool_catalog
from app.providers.generation.openai_compat import OpenAICompatibleGenerationProvider
from tests.test_detective_toolkit_replan import _film


def _node(db, graph_id, key, category, beat, label=None, evidence=None):
    db.add(RevealNode(graph_id=graph_id, node_key=key, label=label or key, category=category,
                      first_revealed_beat_id=beat, first_revealed_order=0, aliases_json="[]",
                      metadata_json=json.dumps({"evidence": evidence or {}})))


def _graph(db_session):
    """Five beats; two dated events (B01, B03), two people, a stored conflict between them (B04) and a question (B05)."""
    case, blueprint, graph = _film(db_session)
    ingest_narration(db_session, blueprint, graph)
    for key in ("ent_a", "ent_b"):
        _node(db_session, graph.id, key, "person", "B01")
    _node(db_session, graph.id, "T001", "timeline", "B01", "He called 911 on the night",
          {"event_date": "2026-02-15"})
    _node(db_session, graph.id, "T002", "timeline", "B03", "The letter arrived", {"event_date": "2026-06"})
    db_session.flush()
    MEM.add_claim(db_session, graph.id, "c_a", "ent_a", "statement", "He says he was asleep", "attributed",
                  supporting_unit_keys=["B03-1"])
    MEM.add_claim(db_session, graph.id, "c_b", "ent_b", "statement", "She saw him awake", "attributed",
                  supporting_unit_keys=["B04-1"])
    MEM.add_claim(db_session, graph.id, "c_f", "ent_b", "fact", "Two casings were found", "established_in_text",
                  supporting_unit_keys=["B02-1"])
    MEM.add_relation(db_session, graph.id, "rel_c", "ent_a", "ent_b", "conflict", "Asleep or awake",
                     supporting_unit_keys=["B04-1"])
    MEM.add_question(db_session, graph.id, "q_gun", ["ent_a"], "Where is the gun", "not_yet_explained",
                     supporting_claim_keys=["B04-1"])
    db_session.commit()
    units = ingest_narration(db_session, blueprint, graph)
    from app.db.models import NarrationUnit

    return case, graph, db_session.query(NarrationUnit).filter_by(graph_id=graph.id).order_by(NarrationUnit.story_order).all()


def _windows(units, seconds=60.0):
    """Every beat lasts `seconds` per sentence: room enough for a card."""
    windows, at = [], 0.0
    for unit in units:
        if not windows or windows[-1]["beat_id"] != unit.beat_id:
            windows.append({"beat_id": unit.beat_id, "start": at, "end": at, "sentences": []})
        windows[-1]["sentences"].append({"start": at, "end": at + seconds, "speech": unit.text})
        windows[-1]["end"] = at + seconds
        at += seconds
    return windows


def test_a_timeline_claim_takes_its_date_from_the_case_timeline_entry_it_cites(db_session):
    _, graph, units = _graph(db_session)
    assert GC.import_timeline_nodes(db_session, graph.id, units) == 2
    assert GC.import_timeline_nodes(db_session, graph.id, units) == 0, "made once"
    from app.db.models import DetectiveClaim

    claim = db_session.query(DetectiveClaim).filter_by(graph_id=graph.id, claim_key="timeline-T001").one()
    assert D.claim_event_date(db_session, claim) == {"year": 2026, "month": 2, "day": 15}


def test_the_graph_makes_a_timeline_an_unknowns_card_and_a_conflict_without_a_model_call(db_session, monkeypatch):
    _, graph, units = _graph(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit.graph_cards, "timeline_events", 2)
    kept = GC.add_graph_cards(db_session, graph.id, "en", units, _windows(units))
    assert kept.get("timeline") == 1 and kept.get("conflict") == 1 and kept.get("unknowns") == 1, kept
    actions = {a.tool_id: a for a in db_session.query(DetectiveToolAction).join(DetectiveScene)
               .filter(DetectiveScene.graph_id == graph.id)}
    assert json.loads(actions["conflict"].content_bindings_json) == ["rel_c", "c_a", "c_b"]
    assert json.loads(actions["unknowns"].content_bindings_json)[-1] == "q_gun"
    assert json.loads(actions["timeline"].content_bindings_json) == ["timeline-T001", "timeline-T002"]


def test_a_card_is_never_placed_before_the_beat_that_tells_what_it_shows(db_session, monkeypatch):
    _, graph, units = _graph(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit.graph_cards, "timeline_events", 2)
    GC.add_graph_cards(db_session, graph.id, "en", units, _windows(units))
    beat_of = {u.unit_key: u.beat_id for u in units}
    for action in db_session.query(DetectiveToolAction).join(DetectiveScene).filter(DetectiveScene.graph_id == graph.id):
        beats = {beat_of[k] for k in json.loads(action.scene.covered_unit_keys_json)}
        assert min(beats) >= {"timeline": "B03", "conflict": "B04", "unknowns": "B04"}[action.tool_id]
    timeline = (db_session.query(DetectiveToolAction).join(DetectiveScene)
                .filter(DetectiveScene.graph_id == graph.id, DetectiveToolAction.tool_id == "timeline").one())
    assert json.loads(timeline.payload_json)["release_fraction"] == FL.FACT_NODE_RELEASE_FRACTION, \
        "claims without a release anchor are shown in the second half of their beat"


def test_the_graph_cards_are_made_once_and_the_fill_cards_can_be_made_again_after_them(db_session, monkeypatch):
    _, graph, units = _graph(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit.graph_cards, "timeline_events", 2)
    first = GC.add_graph_cards(db_session, graph.id, "en", units, _windows(units))
    assert first and GC.add_graph_cards(db_session, graph.id, "en", units, _windows(units)) == {}, "nothing is shown twice"
    FL.fill_with_fact_cards(db_session, graph.id, "en", units, _windows(units))
    made = db_session.query(DetectiveScene).filter(DetectiveScene.graph_id == graph.id,
                                                   DetectiveScene.scene_key.like("fill-%")).count()
    assert made and FL.remove_filler_cards(db_session, graph.id) == made
    assert db_session.query(DetectiveScene).filter(DetectiveScene.graph_id == graph.id,
                                                   DetectiveScene.scene_key.like("graph-%")).count() == sum(first.values())


def test_the_tool_catalog_tells_the_director_exactly_what_each_card_needs_bound():
    catalog = {t["tool_id"]: t for t in build_tool_catalog()}
    assert "sentencing" in catalog and "charge" in catalog["sentencing"]["how_to_bind"]
    assert "exam, sample, method, result and limits" in catalog["forensic"]["how_to_bind"]
    assert "relation of type conflict" in catalog["conflict"]["how_to_bind"]
    assert "how_to_bind" not in catalog["photo"]


def test_only_the_director_is_asked_for_a_limited_reasoning(monkeypatch):
    options = OpenAICompatibleGenerationProvider._request_options
    director = ai_config.generation_for("detective_director")
    strategist = ai_config.generation_for("detective_strategist")
    drafter = ai_config.generation_for("language_draft_writer")
    assert options("gemini-3.8-flash", director, None) == {"reasoning_effort": "low"}
    assert options("gemini-3.8-flash", strategist, None) == {}, "the strategists think as the model does by default"
    assert options("gemini-3.8-flash", drafter, None) == {}, "no other stage changes"


def test_the_memory_of_a_chunk_is_made_once_whether_or_not_it_has_to_be_split(db_session, monkeypatch):
    from app.documentary.detective import director as DD
    from app.providers.generation.base import GenerationError

    case, blueprint, graph = _film(db_session)
    ingest_narration(db_session, blueprint, graph)
    from app.db.models import NarrationUnit

    units = db_session.query(NarrationUnit).filter_by(graph_id=graph.id).order_by(NarrationUnit.story_order).all()
    mapped, directed = [], []

    async def fake_map(db, graph_id, **kw):
        mapped.append(kw["scene_unit_keys"])

    async def fake_direct(db, graph_id, **kw):
        directed.append(kw["scene_unit_keys"])
        if len({k.split("-")[0] for k in kw["scene_unit_keys"]}) > 1:
            raise GenerationError("invalid_output", "too long")

    monkeypatch.setattr(DD, "map_film", fake_map)
    monkeypatch.setattr(DD, "direct_scenes", fake_direct)
    beats = ["B01", "B02", "B03", "B04"]
    keys = [u.unit_key for u in units if u.beat_id in beats]
    done, errors, splits = asyncio.run(toolkit._plan_chunk(db_session, graph.id, "en", 1, beats, keys, units))
    assert done and not errors and splits == 3
    assert len(mapped) == 1 and len(directed) == 1 + 2 + 4, (mapped, directed)


def test_the_catalog_states_the_rules_the_card_builders_enforce():
    from app.documentary.graphics.engines.evidence import EXAMS

    catalog = {t["tool_id"]: t["how_to_bind"] for t in build_tool_catalog() if "how_to_bind" in t}
    assert all(exam in catalog["forensic"] for exam in EXAMS), "the exam claim's text must be one of the exams"
    assert "at least two claims with attribute charge" in catalog["sentencing"]
    assert "at most 90 characters" in catalog["courtroom"] and "at most 90 characters" not in catalog["fact_card"]


def test_a_kind_the_director_cannot_make_is_never_asked_for(monkeypatch):
    missing = toolkit.missing_kinds({"map", "detective_board"}, None)
    assert "floor_plan" not in missing and len([k for k in missing if k != "map"]) == 13
