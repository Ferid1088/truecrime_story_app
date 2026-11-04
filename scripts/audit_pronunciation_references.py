"""Audit of a version's pronunciation references (no model, no voice, nothing written to the database): every word with its reference status, and for
every word without a valid reference the word, its sentence, block and character offsets, the reading as stored and as the ezafe settled it, the
phonemes, the IRAB if any, the ezafe, the validator's problems, the status, the cause and where the reading came from. Writes references.json,
not_valid.csv and summary.json into --out; the story text's hash is taken before and after to show that nothing changed it."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.documentary.pronunciation_explain import load  # noqa: E402
from app.documentary.pronunciation_planner import Planner, settled_reading  # noqa: E402
from app.documentary.reading import doubts as DB  # noqa: E402
from app.documentary.reading.words import tokenize  # noqa: E402


def story_hash(db, version_id: int) -> str:
    return hashlib.sha256((db.get(StoryVersion, version_id).story_text or "").encode("utf-8")).hexdigest()


def provenance(key: str, block_id: str, n: int, voicing, answered: dict) -> str:
    if key in voicing.fixed:
        return "owner approval (every film of the language)"
    if (block_id, n) in answered:
        return "owner's answer to a doubt"
    if voicing.signals.get(block_id) and voicing.signals[block_id][n].overridden:
        return "film table (reading agent), applied by code"
    return "reading agents: editor, then checker and arbiter"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--language", default="fa")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        before = story_hash(db, args.version)
        version, plan, voicing, covered = load(db, args.version, args.language)
        lang = ai_config.voice.for_language(args.language)
        planner = Planner(db, args.language, lang.voice_id, lang.model_id)
        answered = DB.answered(db, version.id)
        rows, statuses, causes, cross = [], Counter(), Counter(), Counter()
        for block in plan["blocks"]:
            if block["block_id"] not in voicing.readings:
                continue
            text = block.get("tts_text") or block["text"]
            bp = planner.plan(block["block_id"], text, voicing, covered=covered)
            words = tokenize(text)
            sentence = " ".join(w.surface + w.after for w in words)
            for w, d, b in zip(words, bp.decisions, bp.boundaries, strict=True):
                statuses[d.reference_status] += 1
                irab = "no reference" if d.reference is None else "IRAB consistent" if d.reference.valid else "IRAB not consistent"
                cross[(d.reference_status, irab)] += 1
                if d.reference_status in ("VALID", "NOT_APPLICABLE"):
                    continue
                causes[(d.reference_status, d.reference_cause)] += 1
                stored = voicing.readings[block["block_id"]][w.n]
                ref = d.reference
                rows.append({"block_id": block["block_id"], "n": w.n, "word": w.surface, "start": w.start, "end": w.end, "sentence": sentence,
                             "reading_stored": stored, "reading_settled": settled_reading(stored, b),
                             "phonemes": " ".join(ref.phonemes) if ref else "", "ipa": ref.ipa if ref else "", "reference_irab": ref.irab if ref else None,
                             "ezafe": b.ezafe_type, "problems": list(ref.problems) if ref else ["no reference"], "status": d.reference_status,
                             "cause": d.reference_cause, "provenance": provenance(w.key, block["block_id"], w.n, voicing, answered),
                             "decision": d.strategy, "voice_input": d.replacement or w.surface})
        after = story_hash(db, args.version)
    (out / "references.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    with (out / "not_valid.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["word"])
        writer.writeheader()
        writer.writerows({**r, "problems": "; ".join(r["problems"])} for r in rows)
    summary = {"version": version.id, "language": args.language, "tokens": sum(statuses.values()),
               "status_by_irab": [{"status": st, "irab": ir, "tokens": n} for (st, ir), n in sorted(cross.items())], "occurrences_not_valid": len(rows),
               "unique_written_forms_not_valid": len({r["word"] for r in rows}), "statuses": dict(statuses),
               "causes": [{"status": s, "cause": c, "occurrences": n, "examples": sorted({r["word"] for r in rows if (r["status"], r["cause"]) == (s, c)})[:6]}
                          for (s, c), n in causes.most_common()],
               "story_sha256_before": before, "story_sha256_after": after, "story_unchanged": before == after}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "causes"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
