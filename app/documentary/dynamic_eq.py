"""Dynamic EQ for narration: reduce harsh bands ONLY when they flare up.

This is not a static EQ. Per band it:

  1. isolates the band with a linear-phase FIR (windowed-sinc, group
     delay compensated — the component lines up sample-for-sample with
     the input, so subtracting it reshapes that band only);
  2. follows short-window RMS envelopes (frame ~20 ms, hop ~5 ms) of the
     band and of a speech-body reference band;
  3. compares band level to the reference ("excess") and calibrates the
     detection threshold adaptively per recording: baseline = a high
     percentile of the excess over active frames + a configured offset.
     A consistently bright voice is treated as normal — only moments
     brighter than THAT voice's own baseline are reduced;
  4. turns the over-threshold excess into gain reduction (a configurable
     share of the excess, capped at the max attenuation);
  5. smooths the reduction with attack/release one-pole envelopes — no
     pumping, clicks or stepped transitions;
  6. applies it as a time-varying band gain: out = in + band·(g−1).

Pitch, formants, voice identity and speed are untouched: processing is
sample-aligned (length is bit-exact) and attenuation-only — the output
can never clip beyond the input. Where no reduction is computed the
output equals the input.

Everything is file-in/file-out and deterministic; results are cached by
input hash + resolved settings, so an unchanged narration is never
re-processed. The sidecar (dynamics.json) documents exactly when, where
and how much gain reduction was applied.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from app.documentary import audio as A

ENGINE_VERSION = "dynamic-eq-1"

_FLOOR = 1e-12  # -240 dBFS floor for dB conversions


# ---------------------------------------------------------------------------
# filter design — windowed-sinc FIR, linear phase, unity gain at band centre
# ---------------------------------------------------------------------------


def bandpass(center_hz: float, bandwidth_hz: float, sr: int,
             taps: int | None = None) -> np.ndarray:
    """Linear-phase FIR bandpass [centre−bw/2, centre+bw/2], normalized
    to ~0 dB gain at the centre frequency."""
    lo = max(20.0, center_hz - bandwidth_hz / 2)
    hi = min(sr / 2 - 100.0, center_hz + bandwidth_hz / 2)
    if hi <= lo:
        raise ValueError(f"Band {center_hz}±{bandwidth_hz / 2} Hz does not fit "
                         f"the {sr} Hz sample rate")
    if taps is None:
        taps = int(sr * 0.025) | 1  # ~25 ms impulse response, odd length
    m = np.arange(taps) - (taps - 1) // 2
    h = (2 * hi / sr * np.sinc(2 * hi / sr * m)
         - 2 * lo / sr * np.sinc(2 * lo / sr * m))
    h *= np.hanning(taps)
    # exact unity gain at the band centre
    w = 2 * np.pi * center_hz / sr
    gain = np.abs(np.sum(h * np.exp(-1j * w * m)))
    return (h / gain) if gain > 0 else h


def band_filter(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """FFT-convolve and remove the group delay: the band component is
    sample-aligned with the input (zero effective phase shift)."""
    n = len(x) + len(h) - 1
    nfft = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(h, nfft), nfft)
    d = (len(h) - 1) // 2
    return np.asarray(y[d:d + len(x)], dtype=np.float32)


# ---------------------------------------------------------------------------
# envelopes and detection
# ---------------------------------------------------------------------------


def _envelope_db(x: np.ndarray, sr: int, frame_ms: float, hop_ms: float):
    """Short-window RMS level (dBFS) at each hop; returns (times, dB)."""
    w = max(1, int(sr * frame_ms / 1000))
    hop = max(1, int(sr * hop_ms / 1000))
    if len(x) <= w:
        starts = np.array([0])
    else:
        starts = np.arange(0, len(x) - w + 1, hop)
        if starts[-1] != len(x) - w:
            starts = np.append(starts, len(x) - w)
    c = np.concatenate(([0.0], np.cumsum(x.astype(np.float64) ** 2)))
    energy = (c[starts + w] - c[starts]) / w
    times = (starts + w / 2) / sr
    return times, 10 * np.log10(np.maximum(energy, _FLOOR))


def _one_pole(target: np.ndarray, hop_s: float, attack_ms: float,
              release_ms: float) -> np.ndarray:
    """Attack/release smoothing of a dB target at hop rate: fast toward
    more reduction, slower back to zero."""
    a = 1.0 - np.exp(-hop_s * 1000.0 / attack_ms)
    r = 1.0 - np.exp(-hop_s * 1000.0 / release_ms)
    out = np.empty(len(target), dtype=np.float64)
    cur = 0.0
    for i, t in enumerate(target):
        cur += (a if t > cur else r) * (t - cur)
        out[i] = cur
    return out


def _events(times: np.ndarray, gr: np.ndarray, min_db: float = 0.5,
            merge_gap_s: float = 0.25) -> list[dict]:
    """Contiguous regions of audible reduction — when, where, how much."""
    on = np.where(gr > min_db)[0]
    if len(on) == 0:
        return []
    groups = np.split(on, np.where(np.diff(on) > 1)[0] + 1)
    hop_s = float(np.median(np.diff(times))) if len(times) > 1 else 0.005
    events, cur = [], None
    for g in groups:
        start, end = float(times[g[0]]), float(times[g[-1]]) + hop_s
        if cur is not None and start - cur["end"] <= merge_gap_s:
            cur["end"] = round(end, 3)
            cur["max_gr_db"] = max(cur["max_gr_db"], round(float(gr[g].max()), 2))
        else:
            if cur is not None:
                events.append(cur)
            cur = {"start": round(start, 3), "end": round(end, 3),
                   "max_gr_db": round(float(gr[g].max()), 2)}
    events.append(cur)
    return events[:500]


def _band_report(times: np.ndarray, gr: np.ndarray, band: dict,
                 baseline: float, threshold: float, act: np.ndarray,
                 hop_ms: float, detected: np.ndarray) -> dict:
    on = gr > 0.1
    return {
        "name": band["name"], "kind": band["kind"],
        "center_hz": band["center_hz"],
        "baseline_db": round(baseline, 2),
        "threshold_db": round(threshold, 2),
        "max_gr_db": round(float(gr.max()), 2),
        "mean_gr_db": round(float(gr[on].mean()), 2) if on.any() else 0.0,
        "active_seconds": round(float(on.sum() * hop_ms / 1000.0), 2),
        # pct_active: envelope engaged (includes release tails);
        # detected_pct: frames the DETECTOR judged excessive — the
        # honest "how often it fires" number.
        "pct_active": round(float((on & act).sum() / act.sum() * 100), 1)
        if act.any() else 0.0,
        "detected_pct": round(float((detected & act).sum()
                                    / act.sum() * 100), 1)
        if act.any() else 0.0,
        "events": _events(times, gr),
        # decimated gain-reduction curve for the UI (~ every 4th hop)
        "curve": [{"t": round(float(t), 3), "gr_db": round(float(g), 2)}
                  for t, g in zip(times[::4], gr[::4])],
    }


def _reduce_band(x: np.ndarray, y: np.ndarray, sr: int, band: dict,
                 ref_db: np.ndarray, times: np.ndarray, active: np.ndarray,
                 ctx: dict) -> dict | None:
    """One detector: band-vs-reference excess → adaptive threshold →
    smoothed gain reduction applied to the band component."""
    xb = band_filter(x, bandpass(band["center_hz"], band["bandwidth_hz"], sr))
    _, band_db = _envelope_db(xb, sr, ctx["frame_ms"], ctx["hop_ms"])
    excess = band_db - ref_db
    if not active.any():
        return {"name": band["name"], "kind": band["kind"],
                "center_hz": band["center_hz"], "active_seconds": 0.0,
                "events": [], "curve": []}

    # Baseline = this voice's normal band-to-body ratio over ALL active
    # speech (quiet-band moments included — they are the normal). A floor
    # stops a band that is basically absent from dragging the threshold
    # down until every flicker counts as harsh.
    baseline = max(
        float(np.percentile(excess[active], ctx["baseline_percentile"])),
        ctx["baseline_floor_db"])
    # Ceiling on the adaptive threshold: sustained excess beyond this is
    # never "just the voice" — a pathological resonance must be treated
    # even when it is constant enough to become its own baseline.
    threshold = min(baseline + band["threshold_offset_db"],
                    ctx["threshold_ceiling_db"])
    over = np.clip(excess - threshold, 0.0, None)
    over[~active] = 0.0
    raw = np.clip(over * band["ratio"] * band["strength"], 0.0,
                  band["max_atten_db"])
    gr = _one_pole(raw, ctx["hop_ms"] / 1000.0,
                   band.get("attack_ms") or ctx["attack_ms"],
                   band.get("release_ms") or ctx["release_ms"])

    # upsample the dB envelope to the audio rate and cut the band part
    g_db = np.interp(np.arange(len(x)) / sr, times, gr)
    y += xb * (np.power(10.0, -g_db / 20.0) - 1.0).astype(np.float32)

    return _band_report(times, gr, band, baseline, threshold, active,
                        ctx["hop_ms"], over > 0)


# ---------------------------------------------------------------------------
# whole-signal processing
# ---------------------------------------------------------------------------


def process(x: np.ndarray, sr: int, settings: dict) -> tuple[np.ndarray, dict]:
    """Dynamic-EQ one mono float signal. `settings` is the resolved
    profile (DynamicEQConfig.for_language().resolved())."""
    lo, hi = settings["reference_band_hz"]
    xr = band_filter(x, bandpass((lo + hi) / 2, hi - lo, sr))
    times, ref_db = _envelope_db(xr, sr, settings["frame_ms"], settings["hop_ms"])
    # Activity = broadband level, not the reference band: during a strong
    # sibilant the midband dips, and that is exactly when detection must
    # stay live.
    _, full_db = _envelope_db(x, sr, settings["frame_ms"], settings["hop_ms"])
    active = full_db > settings["gate_dbfs"]

    ctx = {k: settings[k] for k in
           ("frame_ms", "hop_ms", "gate_dbfs", "baseline_percentile",
            "baseline_floor_db", "threshold_ceiling_db",
            "attack_ms", "release_ms")}

    y = x.copy()
    bands = []
    for b in settings["bands"]:
        rep = _reduce_band(x, y, sr, {**b, "kind": "harsh",
                                      "strength": b["strength"] * settings["strength"]},
                           ref_db, times, active, ctx)
        bands.append(rep)

    de = settings["deesser"]
    de_report = None
    if de["enabled"] and de["strength"] > 0:
        de_report = _reduce_band(x, y, sr, {**de, "name": "deesser",
                                            "kind": "deesser"},
                                 ref_db, times, active, ctx)

    np.clip(y, -1.0, 1.0, out=y)
    seconds = sum(b["active_seconds"] for b in bands)
    if de_report:
        seconds += de_report["active_seconds"]
    return y, {"active_seconds_reduced": round(seconds, 2),
               "bands": bands, "deesser": de_report}


def _manifest_view(report: dict) -> dict:
    """Stats-only copy for the render manifest — curves stay in the
    dynamics sidecar (they are long)."""

    def slim(b):
        return {k: v for k, v in b.items() if k not in ("curve", "events")}

    return {
        **{k: v for k, v in report.items()
           if k not in ("bands", "deesser", "settings", "bands_settings")},
        "settings": {k: report["settings"][k] for k in
                     ("strength", "attack_ms", "release_ms",
                      "baseline_percentile", "threshold_offset_db",
                      "frame_ms", "hop_ms")
                     if k in report.get("settings", {})},
        "bands": [slim(b) for b in report["bands"]],
        "deesser": slim(report["deesser"]) if report.get("deesser") else None,
    }


# ---------------------------------------------------------------------------
# file-level driver (idempotent, lossless intermediate)
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key(src: Path, settings: dict, language: str) -> str:
    payload = json.dumps({"settings": settings, "lang": language,
                          "engine": ENGINE_VERSION,
                          "input": _sha256(src)}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def process_into(src: Path, out_dir: Path, language: str,
                 resolved: dict | None, sr: int) -> dict:
    """Process `src` (the ORIGINAL narration) → narration_enhanced.wav +
    dynamics.json inside `out_dir`. Idempotent: an unchanged input with
    unchanged settings replays its cached report. `resolved=None` (or
    enabled=False) cleans up stale output and reports inactive —
    narration_enhanced.wav existing == enhancement applied."""
    dst = out_dir / "narration_enhanced.wav"
    sidecar = out_dir / "dynamics.json"

    if not resolved or not resolved.get("enabled"):
        dst.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        return {"enabled": False, "applied": False, "active": "original"}

    key = _key(src, resolved, language)
    if sidecar.exists() and dst.exists():
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                cached["cached"] = True
                return _manifest_view(cached)
        except (ValueError, OSError):
            pass

    x = A.decode_f32(src, sr)
    y, rep = process(x, sr, resolved)
    A.write_wav_f32(y, sr, dst)

    report = {
        "key": key, "engine": ENGINE_VERSION, "enabled": True,
        "applied": True, "active": "enhanced", "cached": False,
        "language": language,
        "duration_seconds": round(len(x) / sr, 3),
        "settings": {k: v for k, v in resolved.items() if k != "bands"},
        "bands_settings": resolved["bands"],
        **rep,
    }
    sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    return _manifest_view(report)


def preview_into(src: Path, out_dir: Path, language: str, resolved: dict,
                 seconds: float, sr: int) -> dict:
    """Pre-rendered preview for the studio: process the first `seconds`
    of the ORIGINAL narration with the given (possibly overridden)
    settings → a small mp3 + a report with the full gain-reduction
    curve. Cached by input + settings; never touches the originals."""
    resolved = {**resolved, "_preview_seconds": seconds}
    key = _key(src, resolved, language)
    mp3 = out_dir / f"eq_preview_{key}.mp3"
    side = out_dir / f"eq_preview_{key}.json"
    if mp3.exists() and side.exists():
        try:
            rep = json.loads(side.read_text(encoding="utf-8"))
            rep["cached"], rep["mp3"] = True, mp3.name
            return rep
        except (ValueError, OSError):
            pass

    x = A.decode_f32(src, sr)
    n = min(len(x), int(seconds * sr))
    y, rep = process(x[:n], sr, resolved)
    tmp = out_dir / f"eq_preview_{key}.wav"
    A.write_wav_f32(y, sr, tmp)
    A.encode_mp3(tmp, mp3)
    tmp.unlink(missing_ok=True)

    report = {
        "key": key, "engine": ENGINE_VERSION, "language": language,
        "preview_seconds": round(n / sr, 3), "cached": False, "mp3": mp3.name,
        "settings": {k: v for k, v in resolved.items() if k != "bands"},
        "bands_settings": resolved["bands"], **rep,
    }
    side.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return report
