"""Cuts diverse word clips out of a case's existing narration into the phonetic benchmark (no audio is generated, nothing is billed)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.documentary import phonetic_benchmark as PB  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--case", type=int, required=True)
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--folder", required=True, help="narration folder holding manifest.json, relative to the repository")
    p.add_argument("--language", default="fa")
    args = p.parse_args()
    cache = json.loads((PB.ROOT / "data" / "cases" / str(args.case) / "reading" / f"{args.language}_v{args.version}.json").read_text(encoding="utf-8"))
    with SessionLocal() as db:
        print(json.dumps(PB.extract(db, args.case, args.version, PB.ROOT / args.folder, cache, args.language), indent=2))


if __name__ == "__main__":
    main()
