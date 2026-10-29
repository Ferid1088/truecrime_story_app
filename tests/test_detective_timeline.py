"""The film timeline: every card has its fixed place in its beat, and what is left is the planner's free slot."""
from __future__ import annotations

from app.documentary.detective import timeline as TL


def _window(beat, start, end, sentences=()):
    return {"beat_id": beat, "start": start, "end": end,
            "sentences": [{"start": a, "end": b, "speech": t} for a, b, t in sentences]}


def _record(key, beat, fraction=None, tool="fact_card", text="A short, sourced fact."):
    return {"action_key": key, "beat_id": beat, "tool_id": tool, "purpose": "why", "reuse_reason": None,
            "min_release_fraction": fraction, "data": {"facts": [{"text": text, "fact_id": key}]}}


def test_cards_are_placed_one_after_the_other_inside_their_beat_and_the_rest_is_free():
    windows = [_window("B01", 0.0, 60.0), _window("B02", 60.0, 90.0)]
    timeline = TL.film_timeline(windows, [_record("a1", "B01"), _record("a2", "B01"), _record("a3", "B02")], "en")

    first, second = timeline.cards_by_beat["B01"]
    assert first["start"] == 0.0 and second["start"] == first["end"] and second["end"] <= 60.0
    assert timeline.free_by_beat["B01"] == [(second["end"], 60.0)]
    assert timeline.film_seconds == 90.0 and 0 < timeline.alternative_share < 1
    assert abs(timeline.card_seconds + timeline.free_seconds - 90.0) < 0.2


def test_a_card_waits_for_the_point_where_the_narration_releases_its_claim():
    timeline = TL.film_timeline([_window("B01", 0.0, 100.0)], [_record("a1", "B01", fraction=0.5)], "en")
    card = timeline.cards_by_beat["B01"][0]
    assert card["start"] == 50.0
    assert timeline.free_by_beat["B01"][0] == (0.0, 50.0)


def test_a_card_with_no_room_left_in_its_beat_is_reported_not_squeezed():
    timeline = TL.film_timeline([_window("B01", 0.0, 1.0)], [_record("a1", "B01")], "en")
    assert timeline.cards_by_beat == {} and timeline.skipped[0]["reason"] == "insufficient_reading_time"
    assert timeline.free_by_beat["B01"] == [(0.0, 1.0)]


def test_an_action_the_toolkit_could_not_build_or_with_no_beat_is_reported():
    resolved = [{**_record("a1", "B01"), "skip": "insufficient_data"}, _record("a2", "B99")]
    timeline = TL.film_timeline([_window("B01", 0.0, 60.0)], resolved, "en")
    assert [s["reason"] for s in timeline.skipped] == ["insufficient_data", "beat_not_in_timeline"]
    assert timeline.alternative_share == 0.0


def test_the_planner_gets_slots_only_over_the_free_stretches_and_the_cards_keep_their_time():
    from app.documentary.mvp import direct as DR

    sentences = [(i * 10.0, (i + 1) * 10.0, f"sentence {i}") for i in range(6)]
    window = _window("B01", 0.0, 60.0, sentences)
    timeline = TL.film_timeline([window], [_record("a1", "B01", fraction=0.5)], "en")
    card = timeline.cards_by_beat["B01"][0]
    slots = DR.free_slots(window, timeline.free_by_beat["B01"])

    assert slots and all(s["end"] <= card["start"] + 1e-6 or s["start"] >= card["end"] - 1e-6 for s in slots)
    assert abs(sum(s["end"] - s["start"] for s in slots) + (card["end"] - card["start"]) - 60.0) < 0.01
    pictures = [{"command": "NEW_IMAGE", "share": (s["end"] - s["start"]) / 60.0} for s in slots]
    shots = DR.with_cards(pictures, slots, timeline.cards_by_beat["B01"], 60.0)
    assert [s["command"] for s in shots].count("SHOW_GRAPHIC") == 1 and abs(sum(s["share"] for s in shots) - 1.0) < 1e-6
    fixed = next(s for s in shots if s["command"] == "SHOW_GRAPHIC")
    assert fixed["fixed"] is True and fixed["detective_action_key"] == "a1"
    assert shots.index(fixed) == len([s for s in slots if s["start"] < card["start"]]), "time order"


def test_a_beat_the_cards_fill_completely_leaves_nothing_for_the_planner():
    from app.documentary.mvp import direct as DR

    window = _window("B01", 0.0, 4.0, [(0.0, 4.0, "short")])
    timeline = TL.film_timeline([window], [_record("a1", "B01")], "en")
    if timeline.free_by_beat["B01"]:      # the card holds only as long as it needs: a rest stays free
        assert DR.free_slots(window, timeline.free_by_beat["B01"])
    else:
        assert DR.free_slots(window, []) == []




def test_a_beat_has_no_card_limit_only_the_room_its_narration_leaves_and_cards_never_overlap():
    records = [_record(f"a{i}", "B01", text=f"Sourced fact number {i}.") for i in range(6)]
    timeline = TL.film_timeline([_window("B01", 0.0, 100.0)], records, "en")

    cards = timeline.cards_by_beat["B01"]
    assert len(cards) >= 3 and sum(c["end"] - c["start"] for c in cards) > 70.0, "more than 70% of the beat is cards"
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(cards, cards[1:])) and cards[-1]["end"] <= 100.0
    assert not any(s["reason"] == "beat_card_share_reached" for s in timeline.skipped)


def test_the_detective_board_is_never_drawn_over_a_card_and_its_seconds_count_toward_the_share():
    board = [{"beat_id": "B01", "start": 0.0, "end": 30.0, "new_card_ids": ["c1"], "cumulative_card_ids": ["c1"]}]
    timeline = TL.film_timeline([_window("B01", 0.0, 100.0)], [_record("a1", "B01")], "en", board)

    card = timeline.cards_by_beat["B01"][0]
    assert card["start"] >= 30.0, "the card waits until the board has gone"
    assert timeline.board_seconds == 30.0 and "detective_board" in timeline.kinds
    assert timeline.alternative_share == round((timeline.card_seconds + 30.0) / 100.0, 3)


def test_a_map_is_placed_for_the_time_its_zoom_needs_and_counts_as_a_kind():
    from app.documentary.mvp.direct import map_seconds

    record = {"action_key": "m1", "beat_id": "B01", "tool_id": "map", "purpose": "where it happened",
              "reuse_reason": None, "min_release_fraction": None, "data": {"place": "Cunningham Court"}}
    timeline = TL.film_timeline([_window("B01", 0.0, 60.0)], [record, _record("a1", "B01")], "en")
    slot = timeline.cards_by_beat["B01"][0]
    assert slot["kind"] == "map" and slot["place"] == "Cunningham Court"
    assert abs((slot["end"] - slot["start"]) - map_seconds()) < 0.01
    assert timeline.kinds == {"map", "fact_card"}


def test_a_map_and_a_card_become_the_shots_the_planner_stores_with_the_cases_region():
    from app.documentary.mvp import direct as DR

    window = _window("B01", 0.0, 60.0)
    record = {"action_key": "m1", "beat_id": "B01", "tool_id": "map", "purpose": "where", "reuse_reason": None,
              "min_release_fraction": None, "data": {"place": "Cunningham Court"}}
    timeline = TL.film_timeline([window], [record, _record("a1", "B01")], "en")
    shots = DR.with_cards([], [], timeline.cards_by_beat["B01"], 60.0, "Tipp City, Ohio")
    assert [s["command"] for s in shots] == ["SHOW_MAP", "SHOW_GRAPHIC"] and all(s["fixed"] for s in shots)
    assert shots[0]["map_place"] == "Cunningham Court, Tipp City, Ohio"
