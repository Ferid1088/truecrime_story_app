"""The footage window of a clip: an end card or an intro is cut off at the scene cut it starts with."""
from __future__ import annotations

from app.documentary.mvp import clip_edges as CE


def test_sample_times_cover_the_first_and_the_last_seconds_in_order():
    head, tail = CE.sample_times(0.0, 138.0)
    assert len(head) == len(tail) == 6 and head == sorted(head) and tail == sorted(tail)
    assert 0.0 < head[0] and head[-1] < 15.0 and 123.0 < tail[0] and tail[-1] < 138.0


def test_the_end_card_is_cut_off_at_the_scene_cut_it_starts_with():
    """Case 15's source: an anchor segment at about 131 s, then the QR end card from about 133 s to the end."""
    head, tail = CE.sample_times(0.0, 138.16)
    card = next(t for t in tail if t > 133.0)
    cut = 132.9
    window = CE.window_of({"head_until": None, "tail_from": card, "what": "QR end card"}, 0.0, 138.16, head, tail,
                          [10.0, cut, 40.0])
    assert window.start == 0.0 and window.end == cut and window.what == "QR end card"


def test_with_no_cut_found_the_end_is_the_last_frame_known_to_be_footage():
    head, tail = CE.sample_times(0.0, 138.16)
    window = CE.window_of({"tail_from": tail[3]}, 0.0, 138.16, head, tail, [])
    assert window.end == tail[2] < tail[3]


def test_an_intro_is_cut_off_at_the_scene_cut_after_its_last_frame():
    head, tail = CE.sample_times(0.0, 100.0)
    window = CE.window_of({"head_until": head[1]}, 0.0, 100.0, head, tail, [head[1] + 0.5, head[2] + 1.0])
    assert window.start == head[1] + 0.5 and window.end == 100.0


def test_footage_all_through_or_an_answer_that_names_no_listed_second_changes_nothing():
    head, tail = CE.sample_times(0.0, 60.0)
    assert CE.window_of({"head_until": None, "tail_from": None}, 0.0, 60.0, head, tail, [5.0]) == CE.Window(0.0, 60.0)
    assert CE.window_of({"tail_from": 3.0}, 0.0, 60.0, head, tail, []).end == 60.0, "3 s is not near any tail image"
    assert CE.window_of(None, 0.0, 60.0, head, tail, []).end == 60.0
    assert CE.window_of({"tail_from": "soon"}, 0.0, 60.0, head, tail, []).end == 60.0


def test_a_short_clip_is_not_checked_because_all_of_it_would_be_an_edge():
    assert CE.long_enough_to_check(0.0, 138.0) and not CE.long_enough_to_check(100.0, 105.0)
