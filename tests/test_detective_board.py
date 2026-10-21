"""The detective board: deterministic card selection, layout, continuity
and reading-hold timing (app/documentary/detective_board.py)."""

import json
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary.detective_board import (
    appearances,
    board_cards_for,
    board_layout,
    reading_hold_seconds,
    select_cards,
)

BLUEPRINT = {"beats": [
    {"id": "b1", "purpose": "hook", "reveals": ["T001"], "relies_on": [], "opens": [],
     "answers": [], "unresolved": []},
    {"id": "b2", "purpose": "human_introduction", "reveals": [], "relies_on": [], "opens": [],
     "answers": [], "unresolved": []},
    {"id": "b3", "purpose": "timeline", "reveals": ["T002"], "relies_on": [], "opens": ["Q1"],
     "answers": [], "unresolved": []},
    {"id": "b4", "purpose": "transition", "reveals": [], "relies_on": [], "opens": [],
     "answers": [], "unresolved": []},
    {"id": "b5", "purpose": "contradiction", "reveals": [], "relies_on": ["T002"], "opens": [],
     "answers": [], "unresolved": []},
    {"id": "b6", "purpose": "reveal", "reveals": ["T003"], "relies_on": [], "opens": [],
     "answers": [], "unresolved": []},
]}
EVENTS = [
    {"id": "T001", "date": "2001-01-01", "claim": "A woman is reported missing.",
     "first_beat": "b1"},
    {"id": "T002", "date": "2001-01-02", "claim": "A neighbour says she saw a car.",
     "first_beat": "b3"},
    {"id": "T003", "date": "2001-01-10", "claim": "A second witness changes their account.",
     "first_beat": "b6"},
]


def _cfg(**kw):
    for k, v in kw.items():
        setattr(ai_config.detective_board, k, v)


def test_select_cards_one_per_beat_from_stated_facts(monkeypatch):
    _cfg(min_beats_between_cards=0, max_cards=20)
    cards = select_cards(BLUEPRINT, EVENTS)
    by_beat = {c["beat_id"]: c for c in cards}
    assert by_beat["b1"]["kind"] == "time" and by_beat["b1"]["source_fact"] == "T001"
    assert by_beat["b2"]["kind"] == "person"
    assert by_beat["b5"]["kind"] == "contradiction"
    assert by_beat["b6"]["kind"] == "time"
    # b3 both reveals a dated event and opens a question never answered:
    # the dated-event card wins, never two cards for one beat
    assert by_beat["b3"]["kind"] == "time"
    assert len({c["beat_id"] for c in cards}) == len(cards)


def test_contradiction_links_to_the_earlier_card_it_contradicts():
    _cfg(min_beats_between_cards=0, max_cards=20)
    cards = select_cards(BLUEPRINT, EVENTS)
    contradiction = next(c for c in cards if c["kind"] == "contradiction")
    assert contradiction["links"] and contradiction["links"][0]["relation"] == "contradicts"


def test_min_beats_between_cards_drops_candidates_too_close():
    _cfg(min_beats_between_cards=2, max_cards=20)
    cards = select_cards(BLUEPRINT, EVENTS)
    positions = [i for i, b in enumerate(BLUEPRINT["beats"])
                if b["id"] in {c["beat_id"] for c in cards}]
    assert all(b - a >= 2 for a, b in zip(positions, positions[1:], strict=False))


def test_open_question_never_answered_gets_a_card():
    _cfg(min_beats_between_cards=0, max_cards=20)
    bp = {"beats": [BLUEPRINT["beats"][2]] + [
        {"id": "bx", "purpose": "transition", "reveals": [], "relies_on": [], "opens": [],
         "answers": [], "unresolved": []}]}
    cards = select_cards(bp, [])
    assert any(c["kind"] == "open_question" for c in cards)


def test_board_cards_for_skips_titles_that_never_validated():
    class Row:
        plan_json = '{"cards": [{"id": "c1", "kind": "time", "beat_id": "b1", "status": "new", ' \
                    '"links": [], "title": {"en": "Woman reported missing"}}, ' \
                    '{"id": "c2", "kind": "person", "beat_id": "b2", "status": "new", ' \
                    '"links": [], "title": {}}]}'
        languages_json = '["en"]'

    out = board_cards_for(Row(), "en")
    assert [c["id"] for c in out["cards"]] == ["c1"]
    assert board_cards_for(Row(), "de")["cards"] == []
    assert board_cards_for(None, "en")["cards"] == []


def test_reading_hold_scales_with_title_length_within_bounds():
    _cfg(hold_base_seconds=2.0, hold_per_word_seconds=0.5, hold_min_seconds=3.0,
        hold_max_seconds=6.0)
    short = reading_hold_seconds("One word")
    long = reading_hold_seconds("A considerably longer title than the others here")
    assert 3.0 <= short <= long <= 6.0


def test_board_layout_runs_left_to_right_and_wraps_rows():
    _cfg(cards_per_row=3)
    cards = [{"id": f"c{i}"} for i in range(7)]
    layout = {box["id"]: box for box in board_layout(cards, "en", 1920, 1080)}
    assert layout["c0"]["row"] == 0 and layout["c3"]["row"] == 1 and layout["c6"]["row"] == 2
    row0 = [layout[f"c{i}"]["x"] for i in range(3)]
    assert row0 == sorted(row0)  # left to right


def test_board_layout_is_left_to_right_for_rtl_languages_too():
    _cfg(cards_per_row=3)
    cards = [{"id": f"c{i}"} for i in range(3)]
    en = board_layout(cards, "en", 1920, 1080)
    fa = board_layout(cards, "fa", 1920, 1080)
    assert [c["x"] for c in en] == [c["x"] for c in fa]


def test_appearance_n_contains_every_earlier_card_plus_the_new_ones():
    _cfg(min_card_seconds=2.0, hold_min_seconds=3.0, hold_max_seconds=4.0,
        hold_base_seconds=2.4, hold_per_word_seconds=0.35)
    cards = [{"id": "c1", "title": "First fact"}, {"id": "c2", "title": "Second fact"},
             {"id": "c3", "title": "Third fact"}]
    for c in cards:
        c["beat_id"] = c["id"]
    gaps = {"c1": 5.0, "c2": 0.1, "c3": 5.0}
    apps = appearances(cards, gaps)
    assert apps[0]["cumulative_card_ids"] == ["c1"]
    # c2 had no room: it waits and joins the next appearance that does
    assert apps[1]["cumulative_card_ids"] == ["c1", "c2", "c3"]
    assert apps[1]["new_card_ids"] == ["c2", "c3"]


def test_last_appearance_always_shows_the_full_road_even_with_no_room():
    cards = [{"id": "c1", "title": "First"}, {"id": "c2", "title": "Second"}]
    for c in cards:
        c["beat_id"] = c["id"]
    apps = appearances(cards, {"c1": 0.0, "c2": 0.0})
    assert apps[-1]["cumulative_card_ids"] == ["c1", "c2"]


def test_a_beat_that_relies_on_an_earlier_revealed_fact_links_to_that_card():
    _cfg(min_beats_between_cards=0, max_cards=20)
    bp = {"beats": [
        {"id": "b1", "purpose": "hook", "reveals": ["T001"], "relies_on": [], "opens": [],
         "answers": [], "unresolved": []},
        {"id": "b2", "purpose": "investigation", "reveals": [], "relies_on": ["T001"],
         "opens": [], "answers": [], "unresolved": []},
    ]}
    cards = select_cards(bp, [{"id": "T001", "date": "2001-01-01", "claim": "x",
                               "first_beat": "b1"}])
    assert cards[1]["links"] == [{"to": "card1", "relation": "builds_on"}]


def test_beats_about_the_same_person_are_linked_and_unrelated_ones_are_not():
    _cfg(min_beats_between_cards=0, max_cards=20)
    bp = {"beats": [
        {"id": "b1", "purpose": "human_introduction", "human_focus": {"who": "Abby"},
         "reveals": [], "relies_on": [], "opens": [], "answers": [], "unresolved": []},
        {"id": "b2", "purpose": "evidence", "human_focus": {"who": "Lizzie"},
         "reveals": [], "relies_on": [], "opens": [], "answers": [], "unresolved": []},
        {"id": "b3", "purpose": "investigation", "human_focus": {"who": "Abby"},
         "reveals": [], "relies_on": [], "opens": [], "answers": [], "unresolved": []},
    ]}
    cards = select_cards(bp, [])
    assert cards[0]["links"] == [] and cards[1]["links"] == []
    assert cards[2]["links"] == [{"to": "card1", "relation": "same_person"}]


def test_a_note_keeps_its_place_and_tilt_as_the_board_grows():
    cards = [{"id": f"card{i}"} for i in range(1, 9)]
    few = {box["id"]: box for box in board_layout(cards[:3], "en", 1920, 1080, total=8)}
    many = {box["id"]: box for box in board_layout(cards, "en", 1920, 1080, total=8)}
    for card_id, box in few.items():
        assert many[card_id] == box
    assert all(abs(box["angle"]) <= ai_config.detective_board.max_tilt_degrees
               for box in many.values())


def test_board_notes_stay_inside_the_frame():
    cards = [{"id": f"card{i}"} for i in range(1, 15)]
    for box in board_layout(cards, "en", 1920, 1080, total=14):
        assert box["x"] > 0 and box["x"] + box["w"] < 1920
        assert box["y"] > 0 and box["y"] + box["h"] < 1080


def test_the_shipped_hold_is_long_enough_to_read():
    shipped = json.loads((Path(__file__).parent.parent / "config" / "parameters"
                          / "detective_board.json").read_text())["detective_board"]
    _cfg(**{k: shipped[k] for k in ("hold_base_seconds", "hold_per_word_seconds",
                                    "hold_min_seconds", "hold_max_seconds")})
    assert reading_hold_seconds("Abby") >= 5.0
    assert reading_hold_seconds("Abby and Andrew killed apart") >= 5.0


def _beats(n, purposes):
    return {"beats": [{"id": f"b{i + 1}", "purpose": purposes.get(i + 1, "transition"), "reveals": [],
                       "relies_on": [], "opens": [], "answers": [], "unresolved": []} for i in range(n)]}


def test_the_board_is_planned_to_the_end_of_the_case():
    from app.documentary.detective_board import covers_end

    blueprint = _beats(12, {1: "human_introduction", 2: "orientation", 5: "evidence"})   # nothing card-worthy late
    cards = select_cards(blueprint, [])
    order = [b["id"] for b in blueprint["beats"]]
    assert covers_end(cards, order)
    last = max(order.index(c["beat_id"]) for c in cards)
    assert last == 10 and order[last] != order[-1]            # the tail, but never the very last beat


def test_the_cap_is_kept_when_a_tail_card_is_added(monkeypatch):
    monkeypatch.setattr(ai_config.detective_board, "max_cards", 2)
    monkeypatch.setattr(ai_config.detective_board, "min_beats_between_cards", 1)
    blueprint = _beats(12, {1: "human_introduction", 2: "orientation", 3: "evidence"})
    cards = select_cards(blueprint, [])
    assert len(cards) == 2 and cards[-1]["beat_id"] == "b11"


def test_a_board_that_already_reaches_the_end_is_left_as_it_is():
    blueprint = _beats(12, {1: "human_introduction", 10: "reveal"})
    assert [c["beat_id"] for c in select_cards(blueprint, [])] == ["b1", "b10"]


def test_the_final_stretch_is_configured():
    assert ai_config.detective_board.final_stretch_share == 0.25
