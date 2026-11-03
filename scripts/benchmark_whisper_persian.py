"""Benchmarks a faster-whisper model on real narration blocks with a known script: peak memory, load time, speed (real-time factor) and word error rate.
Nothing is paid and nothing in a film is changed:
python scripts/benchmark_whisper_persian.py MODEL_DIR_OR_NAME CASE VERSION [--blocks 12] [--seed 5] [--out FILE]"""

from __future__ import annotations

import argparse
import json
import random
import resource
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

from app.core.ai_config import ai_config  # noqa: E402
from app.documentary.asr import compare_transcript  # noqa: E402
from app.documentary.voice_render import ROOT, VoiceRenderer  # noqa: E402


def samples(path: Path) -> np.ndarray:
    """The audio as 16 kHz mono floats (decoded by ffmpeg: the installed PyAV cannot be used by faster-whisper's own decoder)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024          # macOS reports bytes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("case", type=int)
    ap.add_argument("version", type=int)
    ap.add_argument("--blocks", type=int, default=12)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    folder = ROOT / "data" / "cases" / str(args.case) / "audio" / "fa" / VoiceRenderer.out_dir_for(args.case, "fa", args.version).name
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    timeline = manifest["timeline"]["blocks"]
    chosen = random.Random(args.seed).sample([b for b in timeline if 8 <= b["end"] - b["start"] <= 30], args.blocks)
    before = rss_mb()
    started = time.time()
    model = WhisperModel(args.model, device="cpu", compute_type=args.compute)
    loaded = time.time() - started
    cfg = ai_config.asr_check.for_language("fa")
    rows, audio_s, decode_s = [], 0.0, 0.0
    for b in chosen:
        wav = ROOT / b["wav"]
        meta = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
        text = meta["text"]
        t0 = time.time()
        segments, info = model.transcribe(samples(wav), language="fa", beam_size=args.beam, vad_filter=False, condition_on_previous_text=False)
        heard = " ".join(s.text.strip() for s in segments)
        took = time.time() - t0
        audio_s += b["end"] - b["start"]
        decode_s += took
        result = compare_transcript(text, heard, "fa", cfg)
        rows.append({"block": b["block_id"], "seconds": round(b["end"] - b["start"], 1), "decode_s": round(took, 1), "wer": result["word_error_rate"], "passed": result["passed"],
                     "heard": heard[:200]})
        print(rows[-1]["block"], rows[-1]["seconds"], "s audio,", rows[-1]["decode_s"], "s decode, wer", rows[-1]["wer"], flush=True)
    summary = {"model": args.model, "compute": args.compute, "beam": args.beam, "load_s": round(loaded, 1), "rss_before_mb": round(before), "peak_rss_mb": round(rss_mb()),
               "blocks": len(rows), "audio_s": round(audio_s, 1), "decode_s": round(decode_s, 1), "real_time_factor": round(decode_s / audio_s, 2),
               "mean_wer": round(sum(r["wer"] for r in rows) / len(rows), 4), "passed": sum(1 for r in rows if r["passed"])}
    print(json.dumps(summary, ensure_ascii=False))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
