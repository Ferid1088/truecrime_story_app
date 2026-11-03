"""Records a reading a person has confirmed as a verified exception (config/reading_exceptions.json), which is also a permanent regression test:
python scripts/confirm_reading.py SURFACE RIGHT [WRONG ...] --by "who, when, where"."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.documentary.reading.transcription import Transcription  # noqa: E402

FILE = Path(__file__).resolve().parents[1] / "config" / "reading_exceptions.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("surface")
    ap.add_argument("right")
    ap.add_argument("wrong", nargs="*")
    ap.add_argument("--by", required=True)
    args = ap.parse_args()
    checked = Transcription().check(args.right)
    if checked.status != "valid":
        raise SystemExit(f"{args.right!r} is not a valid reading ({checked.status}: {checked.fixes or checked.unknown or checked.ambiguous or checked.problems})")
    data = json.loads(FILE.read_text(encoding="utf-8"))
    entry = {"surface": args.surface, "right": [args.right], "wrong": args.wrong, "confirmed": args.by}
    data["exceptions"] = [e for e in data["exceptions"] if e["surface"] != args.surface] + [entry]
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.surface} -> {args.right} recorded; run tests/test_reading_engine.py")


if __name__ == "__main__":
    main()
