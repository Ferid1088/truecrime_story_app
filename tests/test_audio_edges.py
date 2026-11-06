"""The cut of a voiced block: found by the signal (where the sound is), with a fade at each end."""

from __future__ import annotations

import numpy as np

from app.documentary import audio as A

SR = 16000


def tone(path, lead: float, sound: float, tail: float, level: float = 0.3):
    t = np.arange(int(sound * SR)) / SR
    x = np.concatenate([np.zeros(int(lead * SR)), level * np.sin(2 * np.pi * 220 * t), np.zeros(int(tail * SR))]).astype(np.float32)
    A.write_wav_f32(x, SR, path)
    return path


def test_the_sound_of_a_file_is_found_by_its_level_and_a_file_without_sound_has_none(tmp_path):
    src = tone(tmp_path / "a.wav", lead=0.4, sound=1.0, tail=0.6)
    start, end = A.audible_span(src, floor_db=-50)
    assert abs(start - 0.4) < 0.02 and abs(end - 1.4) < 0.02
    assert A.audible_span(tone(tmp_path / "quiet.wav", 0.2, 0.5, 0.2, level=0.0), floor_db=-50) == (0.0, 0.0)


def test_a_cut_with_fades_starts_and_ends_in_silence_and_a_cut_without_them_starts_inside_the_sound(tmp_path):
    src = tone(tmp_path / "a.wav", lead=0.1, sound=1.0, tail=0.1)
    hard, soft = tmp_path / "hard.wav", tmp_path / "soft.wav"
    start = 0.1 + 20.25 / 220                                  # a quarter period into the tone: the signal is at its peak there
    A.to_wav(src, hard, SR, start=start, end=start + 0.6)
    A.to_wav(src, soft, SR, start=start, end=start + 0.6, fade_in=0.02, fade_out=0.03)
    h, s = A.decode_f32(hard, SR), A.decode_f32(soft, SR)
    assert abs(h[0]) > 0.2, "a hard cut inside a sound begins with sound (a click)"
    assert abs(s[:3]).max() < 0.02 and abs(s[-3:]).max() < 0.02, "a faded cut begins and ends in silence"
    assert abs(len(h) - len(s)) <= 2 and abs(s[int(0.1 * SR)]) > 0.1, "the sound in the middle is untouched"


def test_a_file_whose_sound_begins_at_its_first_sample_gets_silence_in_front_and_behind_and_a_soft_start(tmp_path):
    src = tone(tmp_path / "a.wav", lead=0.0, sound=0.5, tail=0.0)
    out = tmp_path / "out.wav"
    A.to_wav(src, out, SR, start=0.0, end=0.5, fade_in=0.012, fade_out=0.03, lead_silence=0.06, tail_silence=0.06)
    x = A.decode_f32(out, SR)
    assert abs(len(x) / SR - 0.62) < 0.01, "0.06 s silence + 0.5 s sound + 0.06 s silence"
    assert abs(x[: int(0.055 * SR)]).max() < 1e-4 and abs(x[-int(0.055 * SR):]).max() < 1e-4, "true silence in front and behind"
    start, end = A.audible_span(out, floor_db=-50)
    assert 0.05 < start < 0.09 and 0.53 < end < 0.58, "the sound sits between them"
