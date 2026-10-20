"""Host-led reel dynamic mix: music volume follows the content (owner decision 3) — lower
while Fereidoun or the narrator speaks, louder in visual-only moments. Driven entirely by the
audio plan's KNOWN speech intervals (never audio detection): a deterministic piecewise-linear
gain envelope (attack down into a speech interval, release back up after it), applied by
multiplying the music waveform sample-for-sample (no ffmpeg filter-graph string juggling, so the
envelope is exactly what was computed, byte for byte). Final loudness is measured with ffmpeg's
``ebur128`` filter (EBU R128), not the ``loudnorm`` analysis pass used elsewhere in the repo,
because the owner asked for ``ebur128`` specifically.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary import audio as A


@dataclass(frozen=True)
class SpeechInterval:
    start: float
    end: float


def build_gain_envelope(speech: list[SpeechInterval], total_seconds: float, *,
                        duck_db: float | None = None, visual_only_db: float | None = None,
                        attack_seconds: float | None = None,
                        release_seconds: float | None = None,
                        fps: int = 50) -> list[tuple[float, float]]:
    """A deterministic (time, gain_db) breakpoint list, one point every ``1/fps`` seconds: the
    base level (``visual_only_db``) everywhere, ramped down to ``duck_db`` over ``attack_seconds``
    before each speech interval starts and ramped back up over ``release_seconds`` after it ends.
    Overlapping ramps (speech intervals closer together than attack+release) simply take the
    lower (more ducked) of the two — music is never briefly loud between two close lines."""
    cfg = ai_config.shorts_host_led
    duck_db = cfg.music_duck_db_under_speech if duck_db is None else duck_db
    visual_only_db = cfg.music_level_db_visual_only if visual_only_db is None else visual_only_db
    attack = cfg.music_duck_attack_seconds if attack_seconds is None else attack_seconds
    release = cfg.music_duck_release_seconds if release_seconds is None else release_seconds
    n = max(int(total_seconds * fps) + 1, 1)

    def target_db(t: float) -> float:
        best = visual_only_db
        for iv in speech:
            if iv.start <= t <= iv.end:
                return duck_db
            if iv.start - attack <= t < iv.start:
                frac = (t - (iv.start - attack)) / attack if attack > 0 else 1.0
                best = min(best, visual_only_db + frac * (duck_db - visual_only_db))
            elif iv.end < t <= iv.end + release:
                frac = (t - iv.end) / release if release > 0 else 0.0
                best = min(best, duck_db + frac * (visual_only_db - duck_db))
        return best

    return [(round(i / fps, 4), round(target_db(i / fps), 3)) for i in range(n)]


def apply_envelope(music_path: Path, envelope: list[tuple[float, float]], out_path: Path,
                   sample_rate: int = 44100) -> Path:
    """Multiply ``music_path``'s samples by the envelope's per-sample linear gain and write
    ``out_path``. The envelope is resampled (linear interpolation between its breakpoints) to
    the music's own sample count, so a 20 Hz control curve still gives a click-free result."""
    import numpy as np

    samples = A.decode_f32(music_path, sample_rate)
    times = np.array([t for t, _ in envelope])
    gains_db = np.array([g for _, g in envelope])
    sample_times = np.arange(len(samples)) / sample_rate
    gains_db_resampled = np.interp(sample_times, times, gains_db,
                                   left=gains_db[0], right=gains_db[-1])
    gains_linear = 10.0 ** (gains_db_resampled / 20.0)
    out = (samples * gains_linear).astype(np.float32)
    A.write_wav_f32(out, sample_rate, out_path)
    return out_path


_EBUR128_SUMMARY = re.compile(
    r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf) LUFS\s*\n.*?"
    r"LRA:\s*([\d.]+) LU\s*\n.*?True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf) dBFS",
    re.DOTALL,
)


def measure_ebur128(path: Path) -> dict:
    """Integrated loudness (LUFS), loudness range (LU) and true peak (dBFS) via ffmpeg's
    ``ebur128`` filter (owner's required tool, distinct from the ``loudnorm`` analysis pass
    used elsewhere in this repo)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    m = _EBUR128_SUMMARY.search(proc.stderr)
    if not m:
        raise A.AudioToolError("ebur128 did not report an Integrated-loudness summary")

    def num(s: str) -> float | None:
        return None if s == "-inf" else float(s)
    return {"integrated_lufs": num(m.group(1)), "lra": float(m.group(2)),
           "true_peak_db": num(m.group(3))}


def duck_headroom_db(envelope: list[tuple[float, float]], speech: list[SpeechInterval],
                     visual_only_db: float | None = None) -> float:
    """The smallest (music level during speech) vs. (visual-only level) gap actually achieved —
    used by the test that verifies music stays at least config dB below speech while it plays."""
    cfg = ai_config.shorts_host_led
    visual_only_db = cfg.music_level_db_visual_only if visual_only_db is None else visual_only_db
    during_speech = [g for t, g in envelope
                     if any(iv.start <= t <= iv.end for iv in speech)]
    if not during_speech:
        return 0.0
    return visual_only_db - max(during_speech)


def generate_synthetic_testbed(out_dir: Path, *, sample_rate: int = 44100
                               ) -> tuple[Path, Path, list[SpeechInterval]]:
    """A licence-free, synthetic stand-in for one reel's audio, built with ffmpeg's own
    signal generators (sine tones for "speech", pink noise for the "music bed") — never real
    content, never to be shipped as or mistaken for a real reel. Returns
    (speech_path, music_path, speech_intervals) for a fixed ~14s layout: two "spoken lines"
    (sine bursts) separated by a "visual-only" gap, matching a hook+outro-shaped reel."""
    out_dir.mkdir(parents=True, exist_ok=True)
    speech_intervals = [SpeechInterval(1.0, 5.0), SpeechInterval(9.0, 13.0)]
    total = 14.0
    speech_path = out_dir / "TESTBED_speech.wav"
    music_path = out_dir / "TESTBED_music.wav"
    # "speech": a 220 Hz tone only inside the speech intervals, silence elsewhere.
    parts = []
    cur = 0.0
    for iv in speech_intervals:
        if iv.start > cur:
            parts.append(f"aevalsrc=0:d={iv.start - cur}:s={sample_rate}")
        parts.append(f"sine=f=220:d={iv.end - iv.start}:r={sample_rate}")
        cur = iv.end
    if cur < total:
        parts.append(f"aevalsrc=0:d={total - cur}:s={sample_rate}")
    filter_in = "".join(f"[{i}:a]" for i in range(len(parts))) + \
        f"concat=n={len(parts)}:v=0:a=1[out]"
    A._run(["ffmpeg", "-y", "-v", "error",
           *[arg for p in parts for arg in ("-f", "lavfi", "-i", p)],
           "-filter_complex", filter_in, "-map", "[out]", str(speech_path)])
    # "music": continuous pink noise for the whole reel, standing in for a music bed.
    A._run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
           "-i", f"anoisesrc=color=pink:duration={total}:sample_rate={sample_rate}",
           str(music_path)])
    return speech_path, music_path, speech_intervals


def plot_envelope(envelope: list[tuple[float, float]], speech: list[SpeechInterval],
                  out_path: Path, *, width: int = 1200, height: int = 400) -> Path:
    """A plain gain-over-time line plot (PIL, no matplotlib dependency in this repo): shaded
    bands for each speech interval, the envelope drawn as a polyline, axis labels in dB/seconds."""
    from PIL import Image, ImageDraw

    cfg = ai_config.shorts_host_led
    lo, hi = cfg.music_duck_db_under_speech - 2, cfg.music_level_db_visual_only + 2
    total = envelope[-1][0] if envelope else 1.0
    margin = 40
    img = Image.new("RGB", (width, height), (18, 18, 20))
    d = ImageDraw.Draw(img)

    def xy(t: float, db: float) -> tuple[float, float]:
        x = margin + (t / total) * (width - 2 * margin) if total else margin
        y = height - margin - (db - lo) / (hi - lo) * (height - 2 * margin)
        return x, y

    for iv in speech:
        x0, _ = xy(iv.start, hi)
        x1, _ = xy(iv.end, hi)
        d.rectangle((x0, margin, x1, height - margin), fill=(70, 30, 30))
    d.line([xy(t, db) for t, db in envelope], fill=(120, 220, 160), width=3)
    d.rectangle((margin, margin, width - margin, height - margin), outline=(90, 90, 90))
    d.text((margin, 8), f"gain envelope, {lo:.0f} to {hi:.0f} dB over {total:.1f}s "
           f"(shaded = speech)", fill=(220, 220, 220))
    img.save(out_path)
    return out_path
