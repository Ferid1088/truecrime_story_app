"""Word-level reading of recognizer output, on synthetic frames. These tests show the mechanics; they validate nothing about a real recognizer."""

from __future__ import annotations

from app.documentary.phoneme_alignment import Window, assign, read, read_block

AHD = {"ahd": "æhd", "ohd": "ohd"}


def frames(*items):
    return [(p, s, e) for p, s, e in items]


def test_each_phone_goes_to_the_word_it_overlaps_most_and_a_gap_phone_to_none():
    windows = [Window(0, "از", 0.0, 0.3), Window(1, "عهد", 0.3, 0.9)]
    got = assign(frames(("a", 0.0, 0.1), ("z", 0.1, 0.28), ("æ", 0.3, 0.5), ("h", 0.5, 0.7), ("d", 0.7, 0.9), ("x", 5.0, 5.1)), windows)
    assert got[0].heard == ["a", "z"] and got[1].heard == ["æ", "h", "d"]


def test_the_recognizers_timestamps_do_not_move_a_phone_into_a_word_it_barely_touches():
    windows = [Window(0, "a", 0.0, 0.5), Window(1, "b", 0.5, 1.0)]
    got = assign(frames(("p", 0.45, 0.62)), windows)
    assert got[1].heard == ["p"] and got[1].boundary_phones == 1


def test_the_nearest_candidate_is_the_recognizers_opinion():
    word = assign(frames(("æ", 0.0, 0.2), ("h", 0.2, 0.4), ("d", 0.4, 0.6)), [Window(0, "عهد", 0.0, 0.6)])[0]
    got = read(word, AHD)
    assert got.choice == "ahd" and "opinion" in got.why


def test_a_word_with_too_little_speech_is_unclear():
    word = assign(frames(("æ", 0.0, 0.05)), [Window(0, "عهد", 0.0, 0.6)])[0]
    assert read(word, AHD).choice == "unclear"


def test_candidates_that_are_equally_near_are_unclear_not_a_guess():
    word = assign(frames(("h", 0.1, 0.4), ("d", 0.4, 0.6)), [Window(0, "عهد", 0.0, 0.6)])[0]      # the vowel was not recognized: both candidates are one edit away
    assert read(word, AHD).choice == "unclear"


def test_only_the_words_with_candidates_are_read():
    windows = [Window(0, "از", 0.0, 0.3), Window(1, "عهد", 0.3, 0.9)]
    got = read_block(frames(("æ", 0.3, 0.5), ("h", 0.5, 0.7), ("d", 0.7, 0.9)), windows, {1: AHD})
    assert [r.window.surface for r in got] == ["عهد"] and got[0].describe()["choice"] == "ahd"
