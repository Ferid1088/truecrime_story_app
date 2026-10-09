"""Thin, testable FFmpeg helpers for the voice pipeline.

Everything is file-in/file-out and deterministic. Loudness follows
EBU R128 via FFmpeg's loudnorm filter; normalization runs two-pass in
linear mode, i.e. one constant gain — word timings never move.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


class AudioToolError(RuntimeError):
    pass


def _run(args: list[str], data: bytes | None = None) -> subprocess.CompletedProcess:
    if not shutil.which(args[0]):
        raise AudioToolError(f"{args[0]} is not installed (required for audio).")
    proc = subprocess.run(args, input=data, capture_output=True,
                          text=data is None)
    if proc.returncode != 0:
        err = proc.stderr
        raise AudioToolError(
            f"{args[0]} failed ({proc.returncode}): "
            f"{(err if isinstance(err, str) else err.decode(errors='replace')).strip()[-400:]}"
        )
    return proc


def probe_duration(path: str | Path) -> float:
    out = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ]).stdout.strip()
    try:
        return float(out)
    except ValueError as e:
        raise AudioToolError(f"Could not read duration of {path}: {out!r}") from e


def to_wav(
    src: str | Path, dst: str | Path, sample_rate: int,
    start: float | None = None, end: float | None = None,
) -> Path:
    """Decode (and optionally trim) to mono 16-bit PCM WAV. Trimming is
    sample-accurate because -ss/-to come after -i."""
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if start is not None:
        args += ["-ss", f"{max(start, 0.0):.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    args += ["-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)]
    _run(args)
    return Path(dst)


def silence_wav(dst: str | Path, seconds: float, sample_rate: int) -> Path:
    _run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=mono", "-t", f"{seconds:.3f}",
        "-c:a", "pcm_s16le", str(dst),
    ])
    return Path(dst)


def concat_wavs(paths: list[str | Path], dst: str | Path) -> Path:
    """Concatenate WAVs that share one format (mono, same rate)."""
    dst = Path(dst)
    listing = dst.with_suffix(".concat.txt")
    listing.write_text(
        "".join(f"file '{Path(p).resolve()}'\n" for p in paths), encoding="utf-8"
    )
    try:
        _run([
            "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c", "copy", str(dst),
        ])
    finally:
        listing.unlink(missing_ok=True)
    return dst


_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def measure_loudness(path: str | Path, target_lufs: float = -16.0,
                     true_peak: float = -1.5, lra: float = 11.0) -> dict:
    """EBU R128 measurement: integrated loudness (LUFS), true peak
    (dBTP), loudness range (LU). Values are floats or None (-inf)."""
    proc = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
        f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}:print_format=json",
        "-f", "null", "-",
    ])
    m = _LOUDNORM_JSON.search(proc.stderr)
    if not m:
        raise AudioToolError("loudnorm did not report measurements")
    data = json.loads(m.group(0))

    def num(key):
        try:
            v = float(data.get(key))
        except (TypeError, ValueError):
            return None
        return None if v in (float("inf"), float("-inf")) else v

    return {
        "integrated_lufs": num("input_i"),
        "true_peak_db": num("input_tp"),
        "lra": num("input_lra"),
        "threshold": num("input_thresh"),
        "target_offset": num("target_offset"),
    }


def normalize_loudness(
    src: str | Path, dst: str | Path, target_lufs: float, true_peak: float,
    lra: float, sample_rate: int, channels: int = 1,
) -> dict:
    """Two-pass loudnorm in linear mode (one constant gain, timing and
    dynamics preserved). Returns the first-pass measurement."""
    m = measure_loudness(src, target_lufs, true_peak, lra)
    if m["integrated_lufs"] is None:
        shutil.copyfile(src, dst)  # silence: nothing to normalize
        return m
    filt = (
        f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}:"
        f"measured_I={m['integrated_lufs']}:measured_TP={m['true_peak_db']}:"
        f"measured_LRA={m['lra']}:measured_thresh={m['threshold']}:"
        f"offset={m['target_offset'] or 0}:linear=true:print_format=summary"
    )
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", filt,
        "-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst),
    ])
    return m


def encode_mp3(src: str | Path, dst: str | Path, bitrate: str = "192k") -> Path:
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(src), "-c:a", "libmp3lame",
        "-b:a", bitrate, str(dst),
    ])
    return Path(dst)


def decode_f32(path: str | Path, sample_rate: int):
    """Decode any audio file to mono float32 samples at `sample_rate`
    (dynamic-EQ input)."""
    import numpy as np

    if not shutil.which("ffmpeg"):
        raise AudioToolError("ffmpeg is not installed (required for audio).")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1",
         "-ar", str(sample_rate), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def write_wav_f32(samples, sample_rate: int, dst: str | Path,
                  codec: str = "pcm_s24le") -> Path:
    """Write float32 samples to a lossless PCM WAV (default 24-bit)."""
    import numpy as np

    _run([
        "ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(sample_rate),
        "-ac", "1", "-i", "-", "-c:a", codec, str(dst),
    ], data=np.asarray(samples, dtype="<f4").tobytes())
    return Path(dst)


def pcm16k_float32(path: str | Path):
    """Decode to 16 kHz mono float32 numpy array (ASR input)."""
    import numpy as np

    if not shutil.which("ffmpeg"):
        raise AudioToolError("ffmpeg is not installed (required for audio).")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000",
         "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)
