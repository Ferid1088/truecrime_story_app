"""The strategy of a whole film: two agents plan it from the text before any scene is directed, the answers are checked,
stored once per graph and handed to the Director; the toolkit then adds scenes for the card kinds the film still misses."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.core.ai_config import ai_config
from app.db.models import DetectiveStrategy
from app.documentary.detective import strategy as ST
from app.documentary.detective import toolkit as TK
from tests.test_detective_filler import _film

KINDS = ST.card_kinds()
OTHERS = [k for k in KINDS if k != "map"]


def _film_strategy(extra=None):
    used = ["map", "detective_board", *OTHERS[:14]]
    data = {"thesis": "Who opened the garage door?",
            "arc": [{"name": "The night", "beats": ["B01", "B02"], "question": "What happened?", "aim": "doubt the intruder"}],
            "kind_roles": {k: {"aim": f"aim of {k}", "use_when": "when it fits", "helps": []} for k in used},
            "synergies": [], "must_use": used}
    return {**data, **(extra or {})}


def _cards_plan(film, beat="B01"):
    return {"plans": {k: [{"beat": beat, "shows": f"{k} at {beat}", "aim": "the aim", "builds_on": None, "then": None}]
                      for k in film["must_use"]}}


def test_there_are_twenty_card_kinds_and_a_photo_or_the_host_is_not_one():
    assert len(KINDS) == 20 and "photo" not in KINDS and "host" not in KINDS
    assert {"map", "detective_board", "fact_card", "timeline"} <= set(KINDS)


def test_a_film_strategy_must_use_the_required_kinds_and_enough_others():
    rule = ST.rules()
    assert ST.validate_film(_film_strategy(), KINDS, rule) == []
    few = _film_strategy({"must_use": ["map", "detective_board", "fact_card"]})
    issues = ST.validate_film(few, KINDS, rule)
    assert any("at least 12" in i for i in issues)
    no_map = _film_strategy()
    no_map["must_use"].remove("map")
    assert any("required kind 'map'" in i for i in ST.validate_film(no_map, KINDS, rule))
    assert any("not a card kind" in i for i in ST.validate_film(_film_strategy({"must_use": ["map", "teleporter"]}), KINDS, rule))
    assert ST.validate_film("nope", KINDS, rule) == ["the answer is not an object"]


def test_a_card_plan_must_name_known_beats_and_place_every_kind_the_film_uses():
    film = _film_strategy()
    assert ST.validate_cards(_cards_plan(film), film, ["B01", "B02"], KINDS) == []
    missing = _cards_plan(film)
    del missing["plans"]["fact_card"]
    assert any("fact_card" in i and "no placement" in i for i in ST.validate_cards(missing, film, ["B01"], KINDS))
    assert any("known beat" in i for i in ST.validate_cards(_cards_plan(film, "B99"), film, ["B01"], KINDS))


def test_the_strategy_is_asked_once_checked_stored_and_reused(db_session, monkeypatch):
    graph, units = _film(db_session)
    case = db_session.get(__import__("app.db.models", fromlist=["Case"]).Case, graph.case_id)
    film = _film_strategy()
    answers = [film, _cards_plan(film)]
    seen = []

    async def ask(db, case_id, agent, label, payload):
        seen.append((agent, payload))
        return answers[len(seen) - 1], None

    monkeypatch.setattr(ST, "_ask", ask)
    strategy = asyncio.run(ST.ensure(db_session, case, graph.id, units, "en"))

    assert [a for a, _ in seen] == ["detective.film_strategist", "detective.card_strategist"]
    assert seen[0][1]["narration"][0]["beat"] == "B01" and seen[0][1]["rules"]["min_kinds"] == 12
    assert seen[1][1]["film_strategy"]["thesis"] == film["thesis"], "the card strategist keeps the film strategist's plan"
    assert strategy.film["must_use"] == film["must_use"] and db_session.query(DetectiveStrategy).filter_by(graph_id=graph.id).count() == 2
    again = asyncio.run(ST.ensure(db_session, case, graph.id, units, "en"))
    assert len(seen) == 2 and again.cards == strategy.cards, "made once, then read"


def test_an_invalid_answer_is_sent_back_once_with_its_issues_and_a_still_invalid_one_means_no_strategy(db_session, monkeypatch):
    graph, units = _film(db_session)
    case = db_session.get(__import__("app.db.models", fromlist=["Case"]).Case, graph.case_id)
    calls = []

    async def ask(db, case_id, agent, label, payload):
        calls.append(payload)
        return {"thesis": ""}, None

    monkeypatch.setattr(ST, "_ask", ask)
    assert asyncio.run(ST.ensure(db_session, case, graph.id, units, "en")) is None
    assert len(calls) == 2 and calls[1]["issues"] and calls[1]["previous_output"] == {"thesis": ""}
    assert db_session.query(DetectiveStrategy).filter_by(graph_id=graph.id).count() == 0


def test_the_director_is_told_the_film_strategy_and_only_the_card_plan_of_its_beats():
    film = _film_strategy()
    plans = {"fact_card": [{"beat": "B01", "shows": "x", "aim": "y"}, {"beat": "B05", "shows": "x", "aim": "y"}],
             "map": [{"beat": "B05", "shows": "x", "aim": "y"}]}
    brief = ST.brief(ST.Strategy(film, {"plans": plans}), {"B01"})
    assert brief["film_strategy"] is film and list(brief["card_plan"]) == ["fact_card"]
    assert brief["card_plan"]["fact_card"] == [plans["fact_card"][0]]
    assert ST.brief(None, {"B01"}) == {}


def test_the_missing_kinds_are_the_required_ones_first_then_the_planned_then_enough_others():
    film = _film_strategy()
    strategy = ST.Strategy(film, {"plans": {}})
    nothing = TK.missing_kinds(set(), strategy)
    assert nothing[:3] == ["map", "detective_board", "timeline"] and len(nothing) == 3 + 10, \
        "the board and the timeline count among the 12"
    others = [k for k in OTHERS if k not in ("detective_board", "timeline")]
    assert TK.missing_kinds({"map", "detective_board", "timeline", *others[:10]}, strategy) == []
    assert TK.missing_kinds({"detective_board", "timeline", *others[:10]}, strategy) == ["map"], \
        "12 kinds with the board and the timeline, the map still required"
    assert TK.missing_kinds({"map", "detective_board", *others[:11]}, strategy) == ["timeline"], "always there"


def test_a_kind_is_asked_for_at_the_beats_the_card_strategist_planned_for_it():
    class U:
        def __init__(self, beat):
            self.beat_id = beat

    units = [U(f"B{n:02d}") for n in range(1, 13)]
    strategy = ST.Strategy(_film_strategy(), {"plans": {"courtroom": [{"beat": "B09"}, {"beat": "B11"}]}})
    assert TK._beats_for("courtroom", strategy, units) == ["B09", "B11"]
    spread = TK._beats_for("sentencing", strategy, units)
    assert len(spread) <= ai_config.detective_toolkit.beats_per_call and spread[0] == "B01"
