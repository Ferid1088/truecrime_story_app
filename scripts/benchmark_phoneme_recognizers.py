"""Benchmarks acoustic phoneme recognizers on labelled clips: does the recognizer's own output (not a forced alignment) lean to the pronunciation the
clip really has? For each clip the recognized phone string is compared with each candidate pronunciation of the word (best matching stretch, edit
distance) and the nearer candidate is the recognizer's call; speed and memory are measured. The labels file says how reliable its labels are.
python scripts/benchmark_phoneme_recognizers.py BACKEND [--labels data/phonetic_benchmark/labels.json] [--out FILE]
BACKEND: allosaurus (run with an interpreter that has allosaurus installed) or wav2vec2 (the project's espeak model)."""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STRIP = set("ʰ̪̃ː ˈˌ.̥ʲ‿")


def normalise(phones: list[str]) -> list[str]:
    out = []
    for p in phones:
        p = "".join(ch for ch in p if ch not in STRIP)
        if p:
            out.append({"ɑ": "ɒ", "a": "æ", "ɐ": "æ", "ɛ": "e", "ɪ": "i", "ʊ": "u", "ɾ": "r", "ɹ": "r", "ɡ": "g", "ɦ": "h"}.get(p, p))
    return out


def best_distance(candidate: list[str], observed: list[str]) -> int:
    """The smallest edit distance between the candidate and any stretch of the observed phones (semi-global alignment)."""
    prev = [0] * (len(observed) + 1)
    for i, c in enumerate(candidate, 1):
        cur = [i] + [0] * len(observed)
        for j, o in enumerate(observed, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (c != o))
        prev = cur
    return min(prev)


def allosaurus_backend():
    from allosaurus.app import read_recognizer

    model = read_recognizer()
    return lambda wav: [line.split()[-1] for line in model.recognize(str(wav), "pes", timestamp=True).splitlines() if line.strip()]


def wav2vec2_backend():
    from app.documentary.pronunciation import get_listener

    listener = get_listener()
    return lambda wav: [f[0] for f in listener.frames(str(wav))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", choices=["allosaurus", "wav2vec2"])
    ap.add_argument("--labels", type=Path, default=ROOT / "data/phonetic_benchmark/labels.json")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    started = time.time()
    recognize = allosaurus_backend() if args.backend == "allosaurus" else wav2vec2_backend()
    loaded = time.time() - started
    rows, decode = [], 0.0
    for c in labels["clips"]:
        t0 = time.time()
        heard = normalise(recognize(args.labels.parent / c["clip"]))
        decode += time.time() - t0
        scores = {name: best_distance(normalise(list(ipa)), heard) for name, ipa in c["candidates"].items()}
        call = min(scores, key=lambda k: (scores[k], k != c["label"]))
        tie = len(set(scores.values())) == 1
        rows.append({"clip": c["clip"], "label": c["label"], "distances": scores, "call": "tie" if tie else call, "correct": (not tie) and call == c["label"],
                     "heard": " ".join(heard)})
        print(c["clip"], "label", c["label"], "distances", scores, "->", rows[-1]["call"], flush=True)
    summary = {"backend": args.backend, "load_s": round(loaded, 1), "decode_s": round(decode, 2), "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576),
               "clips": len(rows), "correct": sum(1 for r in rows if r["correct"]), "ties": sum(1 for r in rows if r["call"] == "tie")}
    print(json.dumps(summary))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "rows": rows, "labels_note": labels["note"]}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
