"""Section F, checklist row F2 - a detective-dispatched graphic is held for the time its own text needs to be
read, never stretched into the next beat's narration, and reports a scheduling request when the beat cannot
give it that time or when unrelated dense narration competes with it. Pure tests over the placement function:
no database, the real graphics layer computes the real reading time."""

from __future__ import annotations

import copy

import pytest

from app.core.ai_config import ai_config
from app.documentary.detective import dispatch as D
from app.documentary.detective import reading as R
from app.documentary.graphics import shots as GS

ONE_LINE = {"facts": [{"text": "The gun was found.", "fact_id": "c0"}]}
DENSE = {"facts": [
    {"text": "The gun was found near the foot of the bed.", "fact_id": "c1"},
    {"text": "Two cartridge casings lay beside it, both spent.", "fact_id": "c2"},
    {"text": "Nothing suggested she had struggled with anyone.", "fact_id": "c3"}]}


def _resolved(data, key="a1"):
    return [{"action_key": key, "tool_id": "fact_card", "purpose": "state the finding", "reuse_reason": None,
             "beat_id": "B01", "data": data}]


def _place(data, start, end, shots=None):
    shots = [] if shots is None else shots
    records = D.place_detective_graphics(shots, [{"beat_id": "B01", "start": start, "end": end}], "en",
                                         _resolved(data))
    return shots, records[0]


def test_a_text_heavy_card_needs_longer_than_a_one_line_card():
    short, dense = R.reading_need("fact_card", ONE_LINE, "en"), R.reading_need("fact_card", DENSE, "en")
    assert dense.words > short.words
    assert dense.planned > short.planned
    assert dense.fastest > short.fastest
    # the old per-kind constant is the same for both: that is what carved a dense card too short
    assert dense.planned > GS.hold_seconds("fact_card")


def test_a_dense_card_is_held_for_its_reading_time_not_the_kind_constant():
    need = R.reading_need("fact_card", DENSE, "en")
    shots, record = _place(DENSE, 0.0, 120.0)
    assert record["result"] == "placed"
    assert "timing_requests" not in record
    assert shots[0]["end"] - shots[0]["start"] == pytest.approx(need.planned, abs=0.01)
    assert shots[0]["end"] - shots[0]["start"] > GS.hold_seconds("fact_card")


def test_a_card_that_cannot_be_read_in_the_room_left_is_reported_not_stretched():
    need = R.reading_need("fact_card", DENSE, "en")
    room = need.fastest - 3.0
    shots, record = _place(DENSE, 0.0, room)
    assert record["skip"] == "insufficient_reading_time"
    assert shots == [], "nothing is carved: a card `finalize` would drop must not be reported as placed"
    request = record["timing_requests"][0]
    assert request["kind"] == "insufficient_reading_time"
    assert request["needed_seconds"] == pytest.approx(need.fastest, abs=0.01)
    assert request["available_seconds"] == pytest.approx(room, abs=0.01)
    assert request["action_key"] == "a1" and request["beat_id"] == "B01"


def test_a_card_squeezed_below_its_planned_time_reports_the_compression_and_stays_in_its_beat():
    need = R.reading_need("fact_card", DENSE, "en")
    room = (need.fastest + need.planned) / 2
    shots, record = _place(DENSE, 10.0, 10.0 + room)
    assert record["result"] == "placed"
    assert shots[0]["end"] <= 10.0 + room + 1e-6, "a card never stretches past its own beat"
    assert record["timing_requests"][0]["kind"] == "reading_time_compressed"
    assert shots[0]["end"] - shots[0]["start"] == pytest.approx(room, abs=0.01)


def _speech_shot(words_per_second, text_words):
    said = " ".join(text_words)
    seconds = len(said.split()) / words_per_second
    return {"kind": "photo", "beat_id": "B01", "start": 0.0, "end": round(seconds, 3), "said": said}


UNRELATED = ("the prosecutor described months of bank transfers between several holding companies and "
             "an offshore account which investigators later traced through two other countries").split()


def test_a_dense_card_over_dense_unrelated_narration_is_flagged_but_still_placed():
    need = R.reading_need("fact_card", DENSE, "en")
    speech = _speech_shot(3.5, UNRELATED * 6)
    assert speech["end"] > need.planned
    shots, record = _place(DENSE, 0.0, speech["end"], [speech])
    assert record["result"] == "placed"
    kinds = [r["kind"] for r in record.get("timing_requests", [])]
    assert "competing_dense_speech" in kinds


def test_narration_about_the_card_does_not_compete_with_it():
    narration = ("the gun was found near the foot of the bed and two cartridge casings lay beside it both "
                 "spent nothing suggested she had struggled with anyone").split()
    speech = _speech_shot(3.5, narration * 6)
    _, record = _place(DENSE, 0.0, speech["end"], [speech])
    assert "competing_dense_speech" not in [r["kind"] for r in record.get("timing_requests", [])]


def test_slow_unrelated_narration_does_not_compete_with_the_card():
    speech = _speech_shot(1.5, UNRELATED * 6)
    _, record = _place(DENSE, 0.0, speech["end"], [speech])
    assert "competing_dense_speech" not in [r["kind"] for r in record.get("timing_requests", [])]


def test_a_one_line_card_is_never_called_dense(monkeypatch):
    speech = _speech_shot(3.5, UNRELATED * 6)
    _, record = _place(ONE_LINE, 0.0, speech["end"], [speech])
    assert "competing_dense_speech" not in [r["kind"] for r in record.get("timing_requests", [])]


def test_the_density_thresholds_come_from_config(monkeypatch):
    speech = _speech_shot(3.5, UNRELATED * 6)
    monkeypatch.setattr(ai_config.detective_toolkit.reading, "dense_card_words", 10_000)
    _, record = _place(DENSE, 0.0, speech["end"], [speech])
    assert "competing_dense_speech" not in [r["kind"] for r in record.get("timing_requests", [])]


def test_without_detective_actions_the_timeline_is_untouched():
    shots = [{"kind": "photo", "beat_id": "B01", "start": 0.0, "end": 30.0, "said": "Something was said."}]
    before = copy.deepcopy(shots)
    assert D.place_detective_graphics(shots, [{"beat_id": "B01", "start": 0.0, "end": 30.0}], "en", []) == []
    assert shots == before


def test_a_placed_dense_card_survives_finalize_where_the_old_constant_hold_was_dropped():
    shots, record = _place(DENSE, 0.0, 120.0)
    assert record["result"] == "placed"
    old_style = copy.deepcopy(shots)
    old_style[0]["end"] = old_style[0]["start"] + GS.hold_seconds("fact_card")
    assert GS.finalize(shots, "en") == [], "the card is held long enough to stream its text"
    dropped = GS.finalize(old_style, "en")
    assert dropped and dropped[0]["type"] == "fact_card", "the pre-F2 hold was too short and was dropped"
