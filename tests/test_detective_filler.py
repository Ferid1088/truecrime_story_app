"""The toolkit's deterministic filler: fact cards from stored, sourced claims, weakest beat first, never invented."""
from __future__ import annotations

import json

from app.core.ai_config import ai_config
from app.db.models import DetectiveScene, DetectiveToolAction, NarrationUnit
from app.documentary import memory as MEM
from app.documentary.detective import buildable as BD
from app.documentary.detective import filler as FL
from tests.test_detective_section_d import _graph


def _film(db_session, beats=("B01", "B02"), words=220, claims_per_beat=4):
    graph, _, _ = _graph(db_session, beats=[{"id": b} for b in beats])
    MEM.ensure_entity(db_session, graph.id, "gun", "the 9mm", "object", beats[0], 0)
    for order, beat in enumerate(beats):
        MEM.add_narration_unit(db_session, graph.id, f"{beat}-1", beat, order, "en", " ".join(["word"] * words))
        for n in range(claims_per_beat):
            MEM.add_claim(db_session, graph.id, f"c-{beat}-{n}", "gun", "origin", f"Established fact {beat} number {n}.",
                          "established_in_text", supporting_unit_keys=[f"{beat}-1"])
    db_session.flush()
    units = db_session.query(NarrationUnit).filter_by(graph_id=graph.id).order_by(NarrationUnit.story_order).all()
    return graph, units


def _bindings(db_session, graph):
    return [json.loads(keys) for (keys,) in db_session.query(DetectiveToolAction.content_bindings_json)
            .join(DetectiveScene, DetectiveScene.id == DetectiveToolAction.scene_id)
            .filter(DetectiveScene.graph_id == graph.id)]


def test_fact_cards_are_made_from_unshown_claims_until_the_share_is_met(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.4)
    graph, units = _film(db_session)
    assert BD.alternative_share(db_session, graph.id, "en", units) == 0.0

    result = FL.fill_with_fact_cards(db_session, graph.id, "en", units)

    assert result["cards"] >= 1 and result["share"] >= 0.4
    assert BD.unbuildable(db_session, graph.id) == []
    shown = [c for keys in _bindings(db_session, graph) for c in keys]
    assert len(shown) == len(set(shown)), "a claim is shown on one card only"
    assert all(len(keys) <= 3 for keys in _bindings(db_session, graph))


def test_a_beat_takes_cards_only_up_to_its_share_of_its_own_narration(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 1.0)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_beat_card_share", 0.5)
    graph, units = _film(db_session, claims_per_beat=12)
    FL.fill_with_fact_cards(db_session, graph.id, "en", units)
    narration, cards = BD.narration_seconds(units), BD.card_seconds_by_beat(db_session, graph.id, "en")
    for beat, seconds in narration.items():
        card = cards.get(beat, 0.0)
        assert card < 0.5 * seconds + 40, "at most one card past the share"


def test_nothing_is_made_when_the_claims_are_all_shown_or_not_grounded(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.9)
    graph, units = _film(db_session, claims_per_beat=0)
    MEM.add_claim(db_session, graph.id, "c-guess", "gun", "origin", "Maybe it was theirs.", "inference",
                  supporting_unit_keys=["B01-1"])
    db_session.flush()
    assert FL.fill_with_fact_cards(db_session, graph.id, "en", units)["cards"] == 0
    assert db_session.query(DetectiveScene).filter_by(graph_id=graph.id).count() == 0


def test_a_second_run_makes_nothing_new(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.4)
    graph, units = _film(db_session)
    first = FL.fill_with_fact_cards(db_session, graph.id, "en", units)
    again = FL.fill_with_fact_cards(db_session, graph.id, "en", units)
    assert first["cards"] >= 1 and again["cards"] == 0


def test_the_share_is_what_the_placed_cards_fill_and_a_beat_never_goes_past_its_cap(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 1.0)
    monkeypatch.setattr(ai_config.detective_toolkit, "max_beat_card_share", 0.3)
    graph, units = _film(db_session, claims_per_beat=12)
    FL.fill_with_fact_cards(db_session, graph.id, "en", units)
    timeline = BD.planned_timeline(db_session, graph.id, "en", units)
    assert timeline.alternative_share == BD.alternative_share(db_session, graph.id, "en", units) <= 0.3 + 1e-6
    narration = BD.narration_seconds(units)
    for beat, seconds in BD.placed_card_seconds_by_beat(timeline).items():
        assert seconds <= 0.3 * narration[beat] + 1e-6
    assert timeline.skipped == [], "every card the filler keeps is placed"


def test_when_the_claims_are_used_up_the_cases_own_facts_of_a_beat_become_cards_placed_in_its_second_half(
        db_session, monkeypatch):
    monkeypatch.setattr(ai_config.detective_toolkit, "min_alternative_share", 0.25)
    graph, units = _film(db_session, claims_per_beat=0)
    for beat in ("B01", "B02"):
        for n in range(3):
            MEM.ensure_entity(db_session, graph.id, f"F{beat}{n}", f"The sourced fact {beat} number {n}.", "fact", beat, n)
    db_session.flush()

    result = FL.fill_with_fact_cards(db_session, graph.id, "en", units)

    assert result["cards"] >= 1 and result["share"] >= 0.25
    timeline = BD.planned_timeline(db_session, graph.id, "en", units)
    for beat, cards in timeline.cards_by_beat.items():
        window = next(w for w in BD.estimated_windows(units) if w["beat_id"] == beat)
        assert all(c["start"] >= window["start"] + 0.5 * (window["end"] - window["start"]) - 1e-6 for c in cards)
    assert FL.import_fact_nodes(db_session, graph.id, units) == 0, "a fact becomes a claim once"
