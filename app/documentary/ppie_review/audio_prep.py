"""Local analysis copies of a voiced take (ffmpeg, free): the original take is read, never written; each copy is mono at the recognizer's rate, the
slower ones time-stretched with `atempo` (pitch kept), all in a temporary folder that is removed when the analysis ends. A slower copy is the same
audio played slower, not another recording, and it does not undo the voice's own speed setting. The pitch of a slower copy is measured against the
original: a copy whose pitch moved more than the configured tolerance is not used."""

from __future__ import annotations

import contextlib
import hashlib
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.ai_config import ai_config


@dataclass(frozen=True)
class Copy:
    speed: float
    path: Path
    pitch_shift_cents: float | None        # median F0 of the copy against the original's (None: too little voiced audio to measure)
    usable: bool


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def samples(path: Path, rate: int):
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def median_pitch(audio, rate: int) -> float | None:
    """The median fundamental frequency of the voiced frames (autocorrelation, config ppie_review.pitch); None when too few frames are voiced."""
    import numpy as np

    cfg = ai_config.ppie_review.pitch
    size, hop, found = int(cfg.frame_s * rate), int(cfg.hop_s * rate), []
    lo, hi = int(rate / cfg.max_hz), int(rate / cfg.min_hz)
    for start in range(0, len(audio) - size, hop):
        frame = audio[start:start + size] - audio[start:start + size].mean()
        energy = float(frame @ frame)
        if energy < cfg.silence * size:
            continue
        corr = np.correlate(frame, frame, "full")[size - 1:]
        lag = lo + int(np.argmax(corr[lo:hi]))
        if corr[lag] / corr[0] > cfg.voicing:
            found.append(rate / lag)
    return float(np.median(found)) if len(found) >= cfg.min_frames else None


def stretch(original: Path, target: Path, speed: float, rate: int) -> None:
    filters = [] if speed == 1.0 else [f"atempo={speed}"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(original), *(["-af", ",".join(filters)] if filters else []), "-ac", "1", "-ar", str(rate),
                    str(target)], check=True)


@contextlib.contextmanager
def analysis_copies(original: Path, parent: Path | None = None):
    """The analysis copies of one take ({speed: Copy}), removed on exit. Raises RuntimeError when the original changed while it was read."""
    cfg = ai_config.ppie_review
    before = digest(original)
    folder = Path(tempfile.mkdtemp(prefix="ppie-analysis-", dir=parent))
    try:
        base_pitch, copies = None, {}
        for speed in cfg.analysis_speeds:
            target = folder / f"{original.stem}__{speed:.2f}.wav"
            stretch(original, target, speed, cfg.analysis_rate)
            pitch = median_pitch(samples(target, cfg.analysis_rate), cfg.analysis_rate)
            if speed == 1.0:
                base_pitch = pitch
            shift = round(1200 * math.log2(pitch / base_pitch), 1) if pitch and base_pitch else None
            copies[speed] = Copy(speed, target, shift, speed == 1.0 or (shift is not None and abs(shift) <= cfg.pitch_tolerance_cents))
        if digest(original) != before:
            raise RuntimeError(f"{original} changed during the analysis")
        yield copies
    finally:
        shutil.rmtree(folder, ignore_errors=True)
