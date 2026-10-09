"""Production validation of the dynamic audio post-processing engine.

For each real ElevenLabs sample (en/de/ar/fa) produces a loudness-matched
A/B/C comparison:

    A  original        — untouched
    B  static_eq       — permanently applied EQ (the rejected approach):
                        fixed -3 dB bells at the two harsh bands plus a
                        fixed -2.5 dB sibilance cut, same FIR machinery
    C  dynamic_eq      — the adaptive engine (config profile per language)

and measures loudness, true peak, harsh-band energy, gain-reduction
activity and events. Listening previews + JSON land in
`Claude outputs/eq_validation/`. Run from the repo root:

    .venv/bin/python scripts/eq_validation.py
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.ai_config import ai_config  # noqa: E402
from app.documentary import audio as A    # noqa: E402
from app.documentary import dynamic_eq as DEQ  # noqa: E402

SR = 44100
OUT = ROOT / "Claude outputs" / "eq_validation"
TARGET_LUFS = -16.0
MAX_SECONDS = 180.0

# Real ElevenLabs speech per language. `mix=True` marks files whose audio
# already carries the music bed — the detector then adapts to programme
# audio, not pure narration (caveat noted in the report).
SAMPLES = {
    "en": [
        ("storyteller_7min",
         ROOT / "Claude outputs/nannup_four_pilot_en_storyteller_7min.mp3",
         False),
        ("voice_comparison",
         ROOT / "Claude outputs/voice_comparison_george_brian_daniel.mp3",
         False),
    ],
    "de": [
        ("inga_pilot", ROOT / "Claude outputs/inga_pilots/Inga_Gehricke_pilot_de.mp4",
         True),
        ("clean_excerpt", ROOT / "Claude outputs/eq_validation/sources/de_clean_excerpt.wav",
         False),
    ],
    "ar": [
        ("inga_pilot", ROOT / "Claude outputs/inga_pilots/Inga_Gehricke_pilot_ar.mp4",
         True),
        ("clean_excerpt", ROOT / "Claude outputs/eq_validation/sources/ar_clean_excerpt.wav",
         False),
    ],
    "fa": [
        ("pronunciation_before_after",
         ROOT / "Claude outputs/persian_pronunciation_before_after.mp3",
         False),
        ("inga_pilot", ROOT / "Claude outputs/inga_pilots/Inga_Gehricke_pilot_fa.mp4",
         True),
        ("clean_excerpt", ROOT / "Claude outputs/eq_validation/sources/fa_clean_excerpt.wav",
         False),
    ],
}


def band_rms_db(x: np.ndarray, center: float, width: float) -> float:
    b = DEQ.band_filter(x, DEQ.bandpass(center, width, SR))
    return float(10 * np.log10(max((b ** 2).mean(), 1e-12)))


def harshness_index(x: np.ndarray) -> dict:
    """Band level relative to the speech-body reference — the detector's
    own 'excess' measure, integrated over the whole file."""
    ref = band_rms_db(x, 1400, 2200)          # 300–2500 Hz body
    return {"ref_db": round(ref, 1),
            "harsh_3k_vs_ref_db": round(band_rms_db(x, 3100, 1500) - ref, 1),
            "harsh_4k7_vs_ref_db": round(band_rms_db(x, 4700, 1500) - ref, 1),
            "sibilant_6k5_vs_ref_db": round(band_rms_db(x, 6500, 4500) - ref, 1)}


def static_eq(x: np.ndarray) -> np.ndarray:
    """Variant B: the permanently-applied EQ this engine replaced —
    fixed gentle cuts whether or not the moment is harsh."""
    y = x.copy()
    for center, width, gain_db in ((3100, 1500, -3.0), (4700, 1500, -3.0),
                                   (6500, 4500, -2.5)):
        xb = DEQ.band_filter(x, DEQ.bandpass(center, width, SR))
        y += xb * (np.float32(10.0 ** (gain_db / 20.0)) - 1.0)
    return y.astype(np.float32)


def loudness_match(samples: np.ndarray, dst: Path) -> dict:
    """Linear gain so the file hits TARGET_LUFS integrated; writes wav."""
    tmp = dst.with_suffix(".prematch.wav")
    A.write_wav_f32(samples, SR, tmp)
    m = A.measure_loudness(tmp, TARGET_LUFS)
    gain = 0.0 if m["integrated_lufs"] is None else TARGET_LUFS - m["integrated_lufs"]
    A.write_wav_f32(np.asarray(samples, dtype=np.float64) * 10 ** (gain / 20.0),
                    SR, dst)
    tmp.unlink(missing_ok=True)
    post = A.measure_loudness(dst, TARGET_LUFS)
    return {"gain_db": round(gain, 2),
            "lufs": post["integrated_lufs"],
            "true_peak_db": post["true_peak_db"],
            "lra": post["lra"]}


def process_sample(lang: str, name: str, src: Path, mixed: bool) -> dict:
    wdir = OUT / lang / name
    wdir.mkdir(parents=True, exist_ok=True)
    wav = wdir / "source.wav"
    if not wav.exists():
        A.to_wav(src, wav, SR, end=MAX_SECONDS)
    x = A.decode_f32(wav, SR)
    dur = len(x) / SR

    resolved = ai_config.dynamic_eq.resolved(lang)
    b_sig = static_eq(x)
    c_sig, rep = DEQ.process(x, SR, resolved)

    variants = {"a_original": x, "b_static_eq": b_sig, "c_dynamic_eq": c_sig}
    out = {"sample": name, "source": src.name, "mixed_music": mixed,
           "duration_s": round(dur, 2), "language": lang, "variants": {},
           "dynamic_eq": rep}
    for label, sig in variants.items():
        vw = wdir / f"{label}.wav"
        meta = loudness_match(sig, vw)
        A.encode_mp3(vw, wdir / f"{label}.mp3")
        vw.unlink(missing_ok=True)
        meta["harshness"] = harshness_index(sig)
        meta["peak_linear"] = round(float(np.abs(sig).max()), 4)
        out["variants"][label] = meta
    (wdir / "dynamics.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def synthetic_battery() -> dict:
    """Requirement checks that need controlled signals."""
    t = np.arange(int(3.0 * SR)) / SR
    base = (0.05 * np.sin(2 * np.pi * 150 * t)
            + 0.03 * np.sin(2 * np.pi * 450 * t)).astype(np.float32)
    s = ai_config.dynamic_eq.resolved("en")
    res = {}

    # constant severe resonance at 3100 — must still be detected
    x = base + 0.5 * np.sin(2 * np.pi * 3100 * t).astype(np.float32)
    y, rep = DEQ.process(x, SR, s)
    res["constant_resonance_3k1"] = {
        "max_gr_db": rep["bands"][0]["max_gr_db"],
        "pct_active_reduced": rep["bands"][0]["pct_active"],
        "events": len(rep["bands"][0]["events"]),
        "detected": rep["bands"][0]["max_gr_db"] > 0,
    }

    # overlap: a resonance at 3900 Hz sits in BOTH bands — cumulative cut
    x = base + 0.4 * np.sin(2 * np.pi * 3900 * t).astype(np.float32)
    y, rep = DEQ.process(x, SR, s)
    bi = DEQ.band_filter(x, DEQ.bandpass(3900, 200, SR))
    bo = DEQ.band_filter(y, DEQ.bandpass(3900, 200, SR))
    res["overlap_3k9_combined"] = {
        "combined_cut_db": round(float(10 * np.log10(
            (bi ** 2).mean() / max((bo ** 2).mean(), 1e-12))), 2),
        "per_band_max": {b["name"]: b["max_gr_db"] for b in rep["bands"]},
    }

    # pumping check on clean speech: GR curve must not flutter
    x, _ = None, None
    rng = np.random.default_rng(1)
    x = (base + 0.008 * rng.standard_normal(len(t))).astype(np.float32)
    x[int(1.0 * SR):int(1.3 * SR)] += (
        0.3 * np.sin(2 * np.pi * 3100 * t[int(1.0 * SR):int(1.3 * SR)]))
    _, rep = DEQ.process(x, SR, s)
    curve = np.array([c["gr_db"] for c in rep["bands"][0]["curve"]])
    res["envelope_smoothness"] = {
        "max_frame_jump_db": round(float(np.abs(np.diff(curve)).max()), 3),
        "note": "hop is 20 ms; 10/120 ms envelopes keep jumps small",
    }
    return res


def main():
    results = {"target_lufs": TARGET_LUFS,
               "static_eq_recipe": "fixed -3dB @3.1k & @4.7k (bw 1.5k), "
                                   "-2.5dB @6.5k (bw 4.5k) — permanent",
               "samples": {}, "synthetic": synthetic_battery()}
    for lang, items in SAMPLES.items():
        for name, src, mixed in items:
            if not src.exists():
                print(f"!! missing {src}", flush=True)
                continue
            print(f".. {lang}/{name}", flush=True)
            r = process_sample(lang, name, src, mixed)
            results["samples"].setdefault(lang, []).append(r)
            for b in r["dynamic_eq"]["bands"]:
                print(f"   {b['name']}: thr={b.get('threshold_db')} "
                      f"max={b['max_gr_db']}dB active={b['pct_active']}% "
                      f"events={len(b['events'])}", flush=True)
            de = r["dynamic_eq"].get("deesser")
            if de:
                print(f"   deesser:  max={de['max_gr_db']}dB "
                      f"active={de['pct_active']}% events={len(de['events'])}",
                      flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done → {OUT}")


if __name__ == "__main__":
    main()
