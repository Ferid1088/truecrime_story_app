"""Dry-run audit of a film's pronunciation dictionary against the text its takes were really sent with (no network, no voice, nothing written
in the film): python scripts/audit_pronunciation_rules.py CASE VERSION [LANG] [OUT.json] [--audio-folder NAME]."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.documentary import voice_prepare as VP  # noqa: E402
from app.documentary.performance import performance_for_version  # noqa: E402
from app.documentary.pronunciation_trace import film_summary, validate  # noqa: E402
from app.documentary.voice_render import ROOT, VoiceRenderer  # noqa: E402

REQUEST_FIELDS = ("text", "voice_id", "model_id", "settings", "seed", "request_id", "language_code", "previous_text", "next_text", "dictionaries", "rules_sent")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=int)
    ap.add_argument("version", type=int)
    ap.add_argument("language", nargs="?", default="fa")
    ap.add_argument("out", nargs="?", default="pronunciation_audit.json")
    ap.add_argument("--audio-folder", help="folder name under the case's audio/<lang>/ (default: the renderer's own folder)")
    args = ap.parse_args()
    folder = ROOT / "data" / "cases" / str(args.case) / "audio" / args.language / (args.audio_folder or VoiceRenderer.out_dir_for(args.case, args.language, args.version).name)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    with SessionLocal() as db:
        version = db.get(StoryVersion, args.version)
        rules = VP.sent_rules(version)
        known = (json.loads(version.narrative_structure or "{}").get("prepared") or {}).get("dictionary") or {}
        plan = {b["block_id"]: b for b in performance_for_version(db, version)["blocks"]}
    fingerprint_now = hashlib.sha256(json.dumps(rules, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    validations, metadata, missing = [], {}, {f: 0 for f in REQUEST_FIELDS}
    for tb in manifest["timeline"]["blocks"]:
        side = ROOT / tb["wav"].replace(".wav", ".json")
        meta = json.loads(side.read_text(encoding="utf-8"))
        block = plan[tb["block_id"]]
        validations.append(validate(tb["block_id"], meta["text"], rules, block.get("tts_text") or block["text"]))
        for f in REQUEST_FIELDS:
            if meta.get(f) in (None, "", [], {}) and f not in ("previous_text", "next_text") or f in ("previous_text", "next_text") and f not in meta:
                missing[f] += 1
        metadata[tb["block_id"]] = {f: f in meta for f in REQUEST_FIELDS}
    summary = film_summary(validations, rules)
    summary.update({"dictionary_id": known.get("id"), "dictionary_version_id": known.get("version_id"), "fingerprint_recorded": known.get("fingerprint"),
                    "fingerprint_of_rules_now": fingerprint_now, "rules_changed_since_dictionary": known.get("fingerprint") != fingerprint_now,
                    "missing_request_metadata_blocks": missing})
    detail = [{"rule": r, "film_status": s, "blocks": {v.block_id: v.checks[i].status for v in validations if v.checks[i].status != "absent"}}
              for i, (r, s) in enumerate(zip(rules, summary["per_rule"], strict=True))]
    blocks = [{"block_id": v.block_id, "changed_after_dictionary": v.changed_after_dictionary, "counts": v.counts(),
               "effective_rules": len(v.effective)} for v in validations]
    Path(args.out).write_text(json.dumps({"summary": summary, "rules": detail, "blocks": blocks}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_rule"}, ensure_ascii=False, indent=1))
    print("block-level checks:", json.dumps({s: sum(v.counts()[s] for v in validations) for s in validations[0].counts()}))
    print("blocks with at least one lost or reconciled rule:", sum(1 for v in validations if v.counts()["unresolved"] or v.counts()["reconciled"]))
    print("blocks with at least one rule acting on their text:", sum(1 for v in validations if v.effective))


if __name__ == "__main__":
    main()
