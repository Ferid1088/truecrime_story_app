"""The toolkit's cards: the analyser, the card makers, the inspector's fix loop, the memory that refuses repeats, the share
the cards must fill, the map and the timeline. The agents are fakes; PostgreSQL is real (`db_session`)."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, DetectiveToolAction, RevealNode
from app.documentary.detective import buildable as BD
from app.documentary.detective import cards as CD
from app.documentary.detective import timeline_cards as TC
from app.documentary.detective import toolkit
from app.documentary.graphics import theme as T
from app.documentary.graphics.timeline import merge_points
from tests.detective_film import film


class FakeAgents:
    """The three agents: the analyser makes a part of every beat, the maker a card from the first sentence of the part, the
    inspector says what the test tells it to."""

    def __init__(self, kinds=("fact_card", "recap"), verdicts=None):
        self.kinds, self.verdicts, self.calls = kinds, list(verdicts or []), []

    async def __call__(self, agent, gen, user, **kw):
        payload = json.loads(user)
        self.calls.append((agent, payload))
        if agent == "detective.text_analyser":
            beats: dict[str, list[str]] = {}
            for u in payload["units"]:
                beats.setdefault(u["unit_key"].split("-")[0], []).append(u["unit_key"])
            covered = set(payload["covered_units"])
            return {"parts": [{"id": f"x{n}", "unit_keys": keys, "summary": f"beat {b}",
                               "cards": [{"kind": k} for k in self.kinds]}
                              for n, (b, keys) in enumerate(beats.items()) if not covered & set(keys)]}, None
        if agent == "detective.card_maker":
            unit = payload["part"]["units"][0]
            text = unit["text"][:30]
            if payload["kind"] == "fact_card":
                return {"card": {"facts": [{"text": text, "fact_id": unit["unit_key"]}]}, "reason": "ok"}, None
            last = payload["part"]["units"][-1]
            return {"card": {"confirmed": [{"text": last["text"][:30], "fact_id": last["unit_key"]}]}, "reason": "ok"}, None
        verdict = self.verdicts.pop(0) if self.verdicts else {"ok": True, "problems": []}
        return verdict, None


@pytest.fixture
def agents(monkeypatch):
    fake = FakeAgents()
    monkeypatch.setattr(CD, "run_agent", fake)
    monkeypatch.setattr(CD, "get_generation_provider", lambda: None)
    return fake


def _actions(db_session, graph, kind):
    return (db_session.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
            .filter(DetectiveScene.graph_id == graph.id, DetectiveToolAction.tool_id == kind).count())


def _case(db_session, graph):
    from app.db.models import Case

    return db_session.get(Case, graph.case_id)


def test_the_analysers_parts_keep_only_existing_units_in_order_and_the_kinds_that_may_be_proposed(db_session):
    _, units = film(db_session)
    raw = {"parts": [
        {"id": "a", "unit_keys": ["B01-1", "B01-2"], "summary": "s", "cards": [{"kind": "fact_card"}, {"kind": "map"}]},
        {"id": "b", "unit_keys": ["B01-2", "B02-1"], "summary": "overlaps the first", "cards": [{"kind": "recap"}]},
        {"id": "c", "unit_keys": ["B09-1"], "summary": "no such unit", "cards": [{"kind": "recap"}]},
        {"id": "d", "unit_keys": ["B03-1"], "summary": "only a kind that is not proposed", "cards": [{"kind": "timeline"}]},
        {"id": "e", "unit_keys": ["B04-1"], "summary": "ok", "cards": [{"kind": "conflict"}]}]}
    parts = CD.valid_parts(raw, units, CD.MAKER_KINDS)
    assert [(p.unit_keys, p.kinds) for p in parts] == [(["B01-1", "B01-2"], ["fact_card"]), (["B04-1"], ["conflict"])]


def test_the_memory_refuses_a_card_that_repeats_the_words_of_an_earlier_one():
    memory = CD.CardMemory()
    memory.add("fact_card", {"facts": [{"text": "Officers found two spent casings near the bed", "fact_id": "B02-1"}]})
    assert memory.repeats({"facts": [{"text": "Two spent casings were found near the bed by officers", "fact_id": "B02-1"}]})
    assert not memory.repeats({"facts": [{"text": "The garage door stood open that morning", "fact_id": "B02-2"}]})
    assert memory.count("fact_card") == 1 and memory.count("recap") == 0


def test_the_inspectors_problems_go_back_to_the_maker_who_fixes_the_card(db_session, agents):
    _, units = film(db_session)
    agents.verdicts = [{"ok": False, "problems": [{"where": "facts[0]", "what": "not in the text", "fix": "use the text"}]}]
    part = CD.Part("P01", ["B02-1", "B02-2"], "casings", ["fact_card"])
    data, why = asyncio.run(CD.make_card(units, part, "fact_card", "en", [], asyncio.Semaphore(2)))
    assert data is not None and why == "ok"
    maker_calls = [p for a, p in agents.calls if a == "detective.card_maker"]
    assert len(maker_calls) == 2 and "repair" not in maker_calls[0]
    assert maker_calls[1]["repair"]["problems"][0]["what"] == "not in the text"
    assert [a for a, _ in agents.calls].count("detective.card_inspector") == 2, "inspected again after the fix"


def test_a_card_that_the_inspector_keeps_refusing_is_dropped_after_the_configured_rounds(db_session, agents, monkeypatch):
    _, units = film(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit, "inspector_rounds", 1)
    bad = {"ok": False, "problems": [{"where": "x", "what": "invented name", "fix": "remove it"}]}
    agents.verdicts = [bad, bad, bad]
    part = CD.Part("P01", ["B02-1"], "casings", ["fact_card"])
    data, why = asyncio.run(CD.make_card(units, part, "fact_card", "en", [], asyncio.Semaphore(2)))
    assert data is None and "invented name" in why
    assert [a for a, _ in agents.calls].count("detective.card_maker") == 2


def test_a_card_that_cites_a_sentence_outside_its_part_is_sent_back(db_session, monkeypatch):
    _, units = film(db_session)
    seen = []

    async def maker(agent, gen, user, **kw):
        payload = json.loads(user)
        seen.append(payload)
        key = "B05-1" if "repair" not in payload else payload["part"]["units"][0]["unit_key"]
        return {"card": {"facts": [{"text": "A fact", "fact_id": key}]}, "reason": "ok"} if agent == "detective.card_maker" \
            else {"ok": True, "problems": []}, None

    monkeypatch.setattr(CD, "run_agent", maker)
    monkeypatch.setattr(CD, "get_generation_provider", lambda: None)
    part = CD.Part("P01", ["B02-1", "B02-2"], "casings", ["fact_card"])
    data, _ = asyncio.run(CD.make_card(units, part, "fact_card", "en", [], asyncio.Semaphore(1)))
    assert data["facts"][0]["fact_id"] == "B02-1"
    assert "not unit keys of this part" in [p for p in seen if "repair" in p][0]["repair"]["problems"][0]["what"]


def test_the_cards_are_made_until_the_minimum_share_is_reached_spread_over_the_film_without_repeats(db_session, agents,
                                                                                                   monkeypatch):
    graph, units = film(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.18)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_alternative_share", 0.40)
    report = asyncio.run(CD.build_cards(db_session, _case(db_session, graph), graph.id, units, "en"))
    assert report.share >= 0.18 and report.made.get("fact_card", 0) + report.made.get("recap", 0) >= 1
    stored = [json.loads(a.payload_json)["data"] for a in db_session.query(DetectiveToolAction).filter(
        DetectiveToolAction.tool_id.in_(["fact_card", "recap"]))]
    texts = [CD.texts_of(d)[0] for d in stored]
    assert len(texts) == len(set(texts)), "no card repeats another"
    assert BD.unbuildable(db_session, graph.id) == []


def test_no_card_is_made_once_the_maximum_share_is_reached(db_session, agents, monkeypatch):
    graph, units = film(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.95)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_alternative_share", 0.35)
    report = asyncio.run(CD.build_cards(db_session, _case(db_session, graph), graph.id, units, "en"))
    assert report.share < 0.50, "stopped near the maximum, not at the minimum asked for"


def test_a_film_has_at_most_the_configured_number_of_fact_cards(db_session, agents, monkeypatch):
    graph, units = film(db_session)
    agents.kinds = ("fact_card",)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_fact_cards", 1)
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.9)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_alternative_share", 0.95)
    asyncio.run(CD.build_cards(db_session, _case(db_session, graph), graph.id, units, "en"))
    assert _actions(db_session, graph, "fact_card") == 1


def test_a_second_run_does_not_repeat_the_cards_of_the_first(db_session, agents, monkeypatch):
    graph, units = film(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.9)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_alternative_share", 0.95)
    case = _case(db_session, graph)
    asyncio.run(CD.build_cards(db_session, case, graph.id, units, "en"))
    first = db_session.query(DetectiveToolAction).count()
    second = asyncio.run(CD.build_cards(db_session, case, graph.id, units, "en"))
    assert db_session.query(DetectiveToolAction).count() == first and second.repeats >= 0


def test_a_stopped_budget_makes_no_agent_call(db_session, agents, monkeypatch):
    graph, units = film(db_session)
    monkeypatch.setattr(CD, "_cap_reached", lambda db, case: "cap reached")
    report = asyncio.run(CD.build_cards(db_session, _case(db_session, graph), graph.id, units, "en"))
    assert report.stopped == "cap reached" and agents.calls == []


def test_the_map_comes_from_a_stored_place_once(db_session):
    graph, units = film(db_session)
    assert CD.ensure_map(db_session, graph.id, units, "en") is True
    assert CD.ensure_map(db_session, graph.id, units, "en") is False
    action = (db_session.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
              .filter(DetectiveScene.graph_id == graph.id, DetectiveToolAction.tool_id == "map").one())
    assert json.loads(action.payload_json)["place"] == "Cunningham Court, Tipp City, Ohio"
    assert BD.issue_of(db_session, action) is None


def test_a_place_that_is_not_in_the_films_beats_still_gives_the_map(db_session):
    graph, units = film(db_session)
    place = db_session.query(RevealNode).filter_by(graph_id=graph.id, node_key="loc1").one()
    place.first_revealed_beat_id, place.category = "B00", "location"
    db_session.flush()
    assert CD.ensure_map(db_session, graph.id, units, "en") is True


def test_a_card_is_shown_after_the_last_sentence_it_covers():
    from app.db.models import NarrationUnit

    units = [NarrationUnit(unit_key=f"B01-{n}", beat_id="B01", story_order=n, language="en", text=" ".join(["w"] * 10))
             for n in (1, 2, 3, 4)]
    assert CD.release_after(units, ["B01-1"]) == 0.25 and CD.release_after(units, ["B01-1", "B01-2"]) == 0.5
    assert CD.release_after(units, ["B01-4"]) == 0.9, "never at the very end of the beat"
    assert CD.spread_order(10)[:3] == [0, 8, 4] and sorted(CD.spread_order(10)) == list(range(10))


def test_the_toolkit_makes_the_map_the_timeline_and_the_cards_and_reports_what_the_film_has(db_session, agents, monkeypatch):
    from tests.test_detective_toolkit_replan import _film as blueprint_film

    case, blueprint, graph = blueprint_film(db_session)
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.1)
    result = asyncio.run(toolkit.invoke(db_session, case, blueprint, graph))
    assert result.enabled and result.called and result.parts >= 1
    assert "map" in {a.tool_id for a in db_session.query(DetectiveToolAction)} or result.missing_kinds
    assert result.scene_count == db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count()


def test_only_the_cheap_calls_run_with_low_reasoning_and_the_inspector_thinks_more():
    from app.providers.generation.openai_compat import OpenAICompatibleGenerationProvider

    options = OpenAICompatibleGenerationProvider._request_options
    maker, inspector = (ai_config.generation_for(r) for r in ("detective_card_maker", "detective_card_inspector"))
    drafter = ai_config.generation_for("language_draft_writer")
    assert options("gemini-3.8-flash", maker, None) == {"reasoning_effort": "low"}
    assert options("gemini-3.8-flash", inspector, None) == {"reasoning_effort": "medium"}
    assert options("gemini-3.8-flash", drafter, None) == {}, "no other stage changes"


def test_the_catalog_names_only_the_kinds_that_remain_and_each_kind_has_rules_for_its_maker():
    from app.documentary.detective.tool_catalog import TOOL_CATALOG

    removed = {"key_facts", "unknowns", "conflict_board", "investigation_clock", "custody", "interrogation", "courtroom",
               "sentencing", "floor_plan"}
    assert not removed & set(TOOL_CATALOG) and not removed & set(ai_config.detective_toolkit.card_kinds)
    assert set(CD.MAKER_KINDS) <= set(ai_config.detective_toolkit.card_kinds)
    assert {"map", "detective_board", "timeline", "fact_card", "recap", "conflict", "evidence_check", "forensic"} <= set(
        TOOL_CATALOG)


# -- the timeline cards ---------------------------------------------------------------------------------------------


def test_events_of_the_same_date_become_one_time_point_and_the_points_run_later():
    events = [{"key": "T2", "label": "Officers found the casings", "date": {"year": 2026, "month": 2, "day": 15}},
              {"key": "T3", "label": "A letter arrived", "date": {"year": 2026, "month": 6}},
              {"key": "T1", "label": "He called 911", "date": {"year": 2026, "month": 2, "day": 15}}]
    points = merge_points(events)
    assert [p["labels"] for p in points] == [["Officers found the casings", "He called 911"], ["A letter arrived"]]
    assert [p["date"]["month"] for p in points] == [2, 6]


def test_a_long_event_text_is_cut_at_a_word_to_the_length_a_point_may_show():
    limit = T.config().timeline["node_text_max_chars"]
    label = merge_points([{"key": "T1", "label": "Botner testified she began an affair with Flynn in March 2024 and more",
                           "date": {"year": 2024, "month": 3}}])[0]["labels"][0]
    assert len(label) <= limit and not label.endswith(" ") and "Flynn" in label and "and more" not in label


def test_the_timeline_cards_are_runs_of_dates_made_once_and_shown_after_their_last_event(db_session):
    graph, units = film(db_session)
    assert TC.build_timeline_cards(db_session, graph.id, units, "en") >= 1
    action = db_session.query(DetectiveToolAction).filter_by(tool_id="timeline").first()
    data = json.loads(action.payload_json)["data"]
    assert [len(p["labels"]) for p in data["points"]] == [2, 1] and BD.issue_of(db_session, action) is None
    beat = json.loads(action.scene.covered_unit_keys_json)[0].split("-")[0]
    assert beat >= "B05", "after the beat that tells the last event"
    assert TC.build_timeline_cards(db_session, graph.id, units, "en") == 0, "made once"
