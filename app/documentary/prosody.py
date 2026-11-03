"""The measurable side of prosody: how fast a block is spoken, where and how long it pauses, how its loudness and coarse pitch move.

It compares takes of the same text. It cannot say that a take sounds natural: it says that a take differs from another beyond the difference between two takes of
the very same request (the noise of the voice), and in which feature. The pitch is a coarse autocorrelation track (numpy only), good for a contour and a range,
not for exact values."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np

from app.core.ai_config import ai_config

RATE = 16000
FEATURES = ("duration_s", "pause_count", "pause_total_s", "voiced_ratio", "energy_std_db", "f0_median_hz", "f0_std_semitones", "f0_range_semitones")


def load(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def frames_of(audio: np.ndarray) -> np.ndarray:
    cfg = ai_config.prosody
    size, hop = int(cfg.frame_s * RATE), int(cfg.hop_s * RATE)
    if len(audio) < size:
        return np.zeros((0, size), np.float32)
    count = 1 + (len(audio) - size) // hop
    index = np.arange(size)[None, :] + hop * np.arange(count)[:, None]
    return audio[index]


def pitch(frame: np.ndarray) -> float | None:
    """The fundamental of one frame by autocorrelation, None when the frame is not periodic enough."""
    cfg = ai_config.prosody
    frame = frame - frame.mean()
    energy = float(np.dot(frame, frame))
    if energy <= 0:
        return None
    corr = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
    low, high = int(RATE / cfg.f0_max_hz), min(int(RATE / cfg.f0_min_hz), len(corr) - 1)
    if high <= low:
        return None
    lag = low + int(np.argmax(corr[low:high + 1]))
    return RATE / lag if corr[lag] / corr[0] >= cfg.voicing_threshold else None


def pauses(silent: np.ndarray) -> list[float]:
    """The lengths (s) of the runs of silent frames that are pauses; the silence at the very start and end is not a pause."""
    cfg = ai_config.prosody
    runs, run, first = [], 0, None
    for i, flag in enumerate(silent):
        if flag:
            run += 1
            first = i if first is None else first
        else:
            if run and first and first > 0:
                runs.append(run * cfg.hop_s)
            run, first = 0, None
    return [r for r in runs if r >= cfg.pause_min_s]


def features(audio: np.ndarray) -> dict:
    cfg = ai_config.prosody
    frames = frames_of(audio)
    if not len(frames):
        return dict.fromkeys(FEATURES, 0.0)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    silent = db < cfg.silence_db
    voiced = [pitch(f) for f, s in zip(frames, silent, strict=True) if not s]
    f0 = np.array([p for p in voiced if p], dtype=float)
    semitones = 12 * np.log2(f0 / np.median(f0)) if len(f0) else np.array([0.0])
    runs = pauses(silent)
    return {"duration_s": round(len(audio) / RATE, 3), "pause_count": float(len(runs)), "pause_total_s": round(sum(runs), 3),
            "voiced_ratio": round(len(f0) / max(1, len(voiced)), 3), "energy_std_db": round(float(db[~silent].std()) if (~silent).any() else 0.0, 3),
            "f0_median_hz": round(float(np.median(f0)), 1) if len(f0) else 0.0, "f0_std_semitones": round(float(semitones.std()), 3),
            "f0_range_semitones": round(float(np.percentile(semitones, 95) - np.percentile(semitones, 5)), 3) if len(f0) else 0.0}


def of_file(path: Path) -> dict:
    return features(load(path))


def differences(a: dict, b: dict) -> dict[str, float]:
    return {k: round(abs(a[k] - b[k]), 4) for k in FEATURES}


def noise_floor(pairs: list[tuple[dict, dict]]) -> dict[str, float]:
    """The largest difference per feature between two takes of the very same request."""
    floor = dict.fromkeys(FEATURES, 0.0)
    for a, b in pairs:
        for k, v in differences(a, b).items():
            floor[k] = max(floor[k], v)
    return floor


def judge(a: dict, b: dict, floor: dict[str, float]) -> dict:
    """The features in which b differs from a by more than `noise_factor` times the noise floor (a feature with no measured noise differs when it differs at all)."""
    factor = ai_config.prosody.noise_factor
    outside = {k: v for k, v in differences(a, b).items() if v > factor * floor[k] and not math.isclose(v, 0.0, abs_tol=1e-9)}
    return {"differs_beyond_noise": sorted(outside), "differences": differences(a, b)}
