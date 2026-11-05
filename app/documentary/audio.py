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
    start: float | None = None, end: float | None = None, tempo: float = 1.0,
    fade_in: float = 0.0, fade_out: float = 0.0,
) -> Path:
    """Decode (and optionally speed up, pitch kept, and trim) to mono 16-bit PCM WAV. Trimming is
    sample-accurate because -ss/-to come after -i; with a tempo, start and end are seconds of the sped-up audio.
    `fade_in` / `fade_out`: seconds of a fade at the cut start and the cut end (a cut that begins or ends inside a sound has no click)."""
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    filters = []
    if tempo != 1.0:
        filters.append(f"atempo={tempo:.4f}")
    lo = max(start or 0.0, 0.0)
    if fade_in > 0:
        filters.append(f"afade=t=in:st={lo:.3f}:d={fade_in:.3f}")
    if fade_out > 0 and end is not None:
        filters.append(f"afade=t=out:st={max(end - fade_out, lo):.3f}:d={fade_out:.3f}")
    if filters:
        args += ["-filter:a", ",".join(filters)]
    if start is not None:
        args += ["-ss", f"{max(start, 0.0):.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    args += ["-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)]
    _run(args)
    return Path(dst)


def audible_span(src: str | Path, floor_db: float, frame_ms: float = 5.0, sample_rate: int = 16000) -> tuple[float, float]:
    """Where the sound of a file is: the first and the last moment louder than `floor_db` (dBFS, RMS of `frame_ms` frames), in seconds of the file as it is.
    The voice's own noise floor and the silence around a take are below it. (0.0, 0.0) for a file with no sound."""
    import numpy as np

    x = decode_f32(src, sample_rate)
    hop = max(1, int(sample_rate * frame_ms / 1000))
    n = len(x) // hop
    if n == 0:
        return 0.0, 0.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    loud = np.nonzero(20 * np.log10(np.maximum(rms, 1e-9)) > floor_db)[0]
    if loud.size == 0:
        return 0.0, 0.0
    return float(loud[0] * hop / sample_rate), float((loud[-1] + 1) * hop / sample_rate)


def extract_audio(video: str | Path, dst: str | Path, sample_rate: int) -> Path:
    """The audio track of a video as mono 16-bit WAV."""
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)])
    return Path(dst)


def to_review_mp3(src: str | Path, dst: str | Path) -> Path:
    """A small mono MP3 of the audio, for the model that listens to it."""
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(dst)])
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
