"""The phoneme recognizer benchmark: the comparison of what a recognizer heard with two candidate pronunciations, and the honesty of its labels."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "benchmark_phoneme_recognizers.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_phones_are_normalised_so_that_the_same_vowel_written_two_ways_is_one_symbol():
    assert bench.normalise(["ɑː", "t̪ʰ", "ɾ", "ˈa", "ɡ"]) == ["ɒ", "t", "r", "æ", "g"]


def test_the_distance_is_to_the_best_stretch_of_what_was_heard_not_to_the_whole_sentence():
    heard = list("xxxʔohdhɒjixxx")
    assert bench.best_distance(list("ʔohdhɒji"), heard) == 0
    assert bench.best_distance(list("ʔæhdhɒji"), heard) == 1


def test_equal_distances_for_both_candidates_are_a_tie_in_the_call():
    scores = {"ahd": 5, "ohd": 5}
    assert len(set(scores.values())) == 1, "the benchmark reports such a clip as a tie, never as a right call"


def test_the_labels_say_how_little_they_are_and_every_clip_has_two_candidates_and_a_label_among_them():
    labels = json.loads((ROOT / "data/phonetic_benchmark/labels.json").read_text(encoding="utf-8"))
    assert "NOT a validated reference set" in labels["note"]
    for clip in labels["clips"]:
        assert clip["label"] in clip["candidates"] and len(clip["candidates"]) == 2 and clip["how"]
