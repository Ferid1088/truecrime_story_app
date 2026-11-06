"""The toolkit's cards are in the film's language: a card that is not is isolated (kept, read by nobody) and another is made in its
place, and the card makers are told that Latin words in a Persian or Arabic card are a problem. PostgreSQL is real (`db_session`)."""
from __future__ import annotations

import json

from app.db.models import DetectiveScene, DetectiveToolAction
from app.documentary.detective import buildable as BD
from app.documentary.detective import cards as CD
from app.documentary.detective import dispatch as D
from app.documentary.detective import language_gate as LG
from app.documentary.detective import timeline_cards as TC
from tests.detective_film import film
from tests.test_detective_cards import _chapter_plan


def _of_graph(db_session, graph, kind):
    return (db_session.query(DetectiveToolAction).join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
            .filter(DetectiveScene.graph_id == graph.id, DetectiveToolAction.tool_id == kind).all())


def _card(db_session, graph, units, kind, data, language=None):
    part = CD.Part("P1", [units[0].unit_key], "x", [kind])
    action = CD.store_card(db_session, graph.id, part, kind, data, units, language or "fa", CD.next_serial(db_session, graph.id))
    if language is None:                              # a card stored before the language was kept
        payload = json.loads(action.payload_json)
        payload.pop("language")
        action.payload_json = json.dumps(payload, ensure_ascii=False)
    return action


def test_latin_words_in_a_persian_card_are_a_problem_for_its_maker_and_digits_and_persian_are_not():
    assert LG.script_problem("fact_card", {"facts": [{"text": "کالب با American Idol آشنا شد", "fact_id": "a"}]}, "fa")
    assert LG.script_problem("fact_card", {"facts": [{"text": "کالب ۹۱۱ را گرفت", "fact_id": "a"}]}, "fa") is None
    assert LG.script_problem("fact_card", {"facts": [{"text": "He called 911", "fact_id": "a"}]}, "de") is None, \
        "a Latin-script language cannot be told apart by script"
    assert CD._problem_of("fact_card", {"facts": [{"text": "Caleb Flynn called", "fact_id": "a"}]}, "ar") is not None


def test_a_card_in_another_language_is_isolated_not_deleted_and_nothing_reads_it_again(db_session):
    graph, units = film(db_session)
    english = _card(db_session, graph, units, "fact_card", {"facts": [{"text": "He called 911", "fact_id": "B01-1"}]}, "en")
    persian = _card(db_session, graph, units, "fact_card", {"facts": [{"text": "او با ۹۱۱ تماس گرفت", "fact_id": "B01-1"}]})
    legacy = _card(db_session, graph, units, "fact_card", {"facts": [{"text": "او زنگ زد", "fact_id": "B01-2"}]}, None)
    isolated = LG.isolate_foreign_cards(db_session, graph.id, "fa")
    assert [i["action_key"] for i in isolated] == [english.action_key] and "written in en" in isolated[0]["why"]
    assert english.tool_id == D.ISOLATED and json.loads(english.payload_json)["isolated"]["tool"] == "fact_card"
    assert persian.tool_id == "fact_card" and legacy.tool_id == "fact_card", "cards in the language, stored with or without a marker, stay"
    assert db_session.get(DetectiveToolAction, english.id) is not None, "kept, not deleted"
    assert BD.issue_of(db_session, english) is None
    assert next(r for r in D.resolve_dispatchable_actions(db_session, graph.id)
                if r["action_key"] == english.action_key)["skip"] == "isolated"
    assert CD.CardMemory.of_film(db_session, graph.id).cards and all(
        "called" not in " ".join(words) for _, words, _ in CD.CardMemory.of_film(db_session, graph.id).cards)


def test_a_persian_card_with_latin_words_is_isolated_too(db_session):
    graph, units = film(db_session)
    leaky = _card(db_session, graph, units, "fact_card", {"facts": [{"text": "کالب و American Idol", "fact_id": "B01-1"}]})
    assert [i["action_key"] for i in LG.isolate_foreign_cards(db_session, graph.id, "fa")] == [leaky.action_key]


def test_an_english_timeline_is_isolated_and_a_new_one_is_made_in_the_films_language_beside_it(db_session):
    graph, units = film(db_session)
    assert TC.build_timeline_cards(db_session, graph.id, units, "en") >= 1          # the old card: the graph's English labels
    old = _of_graph(db_session, graph, "timeline")[0]
    payload = json.loads(old.payload_json)
    payload.pop("language")                                                         # as cards stored before the language was kept
    old.payload_json = json.dumps(payload)
    db_session.commit()
    _chapter_plan(db_session, graph, {"T001": "تماس با ۹۱۱", "T002": "پوکه‌ها پیدا شد", "T003": "رسیدن نامه"})
    assert [i["kind"] for i in LG.isolate_foreign_cards(db_session, graph.id, "fa")] == ["timeline"]
    assert TC.build_timeline_cards(db_session, graph.id, units, "fa") >= 1, "a new row is made though the old one is kept"
    (fresh,) = _of_graph(db_session, graph, "timeline")
    assert fresh.id != old.id and old.tool_id == D.ISOLATED and fresh.scene.scene_key != old.scene.scene_key
    labels = [label for p in json.loads(fresh.payload_json)["data"]["points"] for label in p["labels"]]
    assert labels == ["تماس با ۹۱۱", "پوکه‌ها پیدا شد", "رسیدن نامه"]


def test_the_toolkit_reports_the_cards_it_isolated(monkeypatch, db_session):
    import asyncio

    from app.documentary.detective import toolkit
    from tests.test_detective_toolkit_replan import _film as blueprint_film

    case, blueprint, graph = blueprint_film(db_session)
    monkeypatch.setattr(LG, "isolate_foreign_cards", lambda db, graph_id, language: [{"action_key": "a", "kind": "x", "why": "y"}])
    monkeypatch.setattr(CD, "build_cards", lambda *a, **k: _empty_report())
    result = asyncio.run(toolkit.invoke(db_session, case, blueprint, graph))
    assert result.isolated and "isolated" in result.reason


async def _empty_report():
    return CD.CardsReport()
