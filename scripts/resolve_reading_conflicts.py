"""Resolves the words a version's narration reads in more than one way, by the rules of reading/resolution.py (never by frequency or a model's
confidence), and writes the owner-review report of what the evidence cannot decide (no model, no voice). With --apply the resolutions are kept for the
case (reading_resolutions): the film table applies them, and only the blocks that contain a resolved word are read again."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.documentary.pronunciation_explain import read  # noqa: E402
from app.documentary.reading import resolution as RES  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--language", default="fa")
    p.add_argument("--out", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        loaded = read(db, args.version, args.language)
        families = RES.analyse(db, loaded, args.language)
        review = RES.review(db, loaded, args.language, families)
        saved = RES.save(db, loaded.version.case_id, args.language, families) if args.apply else 0
    report = {"families": len(families), "by_status": {s: sum(f.status == s for f in families) for s in ("resolved", "retained_variant", "unresolved")},
              "by_rule": {r: sum(f.rule == r for f in families) for r in sorted({f.rule for f in families})},
              "resolved": [f.describe() for f in families if f.status == "resolved"],
              "retained": [f.describe() for f in families if f.status == "retained_variant"], "owner_review": review, "rows_saved": saved}
    (out / "resolution.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("families", "by_status", "by_rule", "rows_saved")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
