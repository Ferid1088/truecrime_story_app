"""F1: a pilot that covers every beat narrates the whole film — the stored FULL spoken
version (pilot_beat_ids=None) is the same text and is reused, not rewritten. A real subset
pilot still rewrites."""
from app.documentary.jobs import reusable_spoken


def test_full_spoken_is_reused_when_the_pilot_covers_every_beat():
    saved = {"pilot_seconds": None, "pilot_beat_ids": None}   # the full-film spoken
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}]}
    assert reusable_spoken(saved, pilot_seconds=500, focus_beats=["B01", "B02"],
                           blueprint=blueprint) is True


def test_full_spoken_is_not_reused_for_a_real_subset_pilot():
    saved = {"pilot_seconds": None, "pilot_beat_ids": None}
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}, {"id": "B03"}]}
    assert reusable_spoken(saved, pilot_seconds=120, focus_beats=["B01"],
                           blueprint=blueprint) is False


def test_an_exact_pilot_match_is_reused():
    saved = {"pilot_seconds": 120.0, "pilot_beat_ids": ["B01", "B02"]}
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}, {"id": "B03"}]}
    assert reusable_spoken(saved, pilot_seconds=120, focus_beats=["B01", "B02"],
                           blueprint=blueprint) is True


def test_a_different_pilot_cut_is_rewritten():
    saved = {"pilot_seconds": 120.0, "pilot_beat_ids": ["B01", "B02"]}
    blueprint = {"beats": [{"id": "B01"}, {"id": "B02"}, {"id": "B03"}]}
    assert reusable_spoken(saved, pilot_seconds=120, focus_beats=["B01", "B03"],
                           blueprint=blueprint) is False


def test_a_spoken_without_stored_cut_info_is_rewritten():
    blueprint = {"beats": [{"id": "B01"}]}
    assert reusable_spoken({}, pilot_seconds=100, focus_beats=["B01"],
                           blueprint=blueprint) is False
