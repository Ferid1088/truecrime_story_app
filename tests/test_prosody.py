"""Prosody features on synthetic audio: timing, pauses, energy, a coarse pitch. They show the measurements work, not that a take sounds natural."""

from __future__ import annotations

import numpy as np
import pytest

from app.documentary import prosody as P


def tone(hz: float, seconds: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * P.RATE)) / P.RATE
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * P.RATE), np.float32)


def test_the_pitch_of_a_steady_tone_is_found_within_a_few_percent():
    got = P.features(tone(200, 1.0))
    assert got["f0_median_hz"] == pytest.approx(200, rel=0.05) and got["voiced_ratio"] > 0.9 and got["f0_range_semitones"] < 1.0


def test_a_pause_between_two_stretches_of_speech_is_counted_and_the_edges_are_not():
    got = P.features(np.concatenate([silence(0.4), tone(200, 1.0), silence(0.6), tone(200, 1.0), silence(0.4)]))
    assert got["pause_count"] == 1 and got["pause_total_s"] == pytest.approx(0.6, abs=0.05)


def test_a_short_silence_is_not_a_pause():
    assert P.features(np.concatenate([tone(200, 1.0), silence(0.1), tone(200, 1.0)]))["pause_count"] == 0


def test_a_rising_pitch_has_a_range():
    rising = np.concatenate([tone(150, 0.5), tone(200, 0.5), tone(300, 0.5)])
    assert P.features(rising)["f0_range_semitones"] > 6


def test_a_difference_counts_only_beyond_the_noise_between_takes_of_the_same_request():
    a = P.features(tone(200, 1.0))
    same = P.features(tone(202, 1.0))
    other = P.features(np.concatenate([tone(200, 1.0), silence(0.6), tone(200, 1.0)]))
    floor = P.noise_floor([(a, same)])
    assert P.judge(a, same, floor)["differs_beyond_noise"] == []
    assert {"pause_count", "pause_total_s", "duration_s"} <= set(P.judge(a, other, floor)["differs_beyond_noise"])


def test_audio_shorter_than_a_frame_has_zero_features():
    assert set(P.features(np.zeros(10, np.float32)).values()) == {0.0}
