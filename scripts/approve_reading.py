#!/usr/bin/env python3
"""Fix how one word of a spoken language is read, for every film of the language: the voice is given it as IPA (app/documentary/reading/voicing.py).

    python scripts/approve_reading.py fa عهد "'ahd" [--kind spoken_form]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import ReadingApproval  # noqa: E402
from app.documentary.reading.words import key_of  # noqa: E402

KINDS = ("person", "place", "foreign", "homograph", "spoken_form")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("language")
    parser.add_argument("surface")
    parser.add_argument("latin")
    parser.add_argument("--kind", choices=KINDS, default="spoken_form")
    args = parser.parse_args()
    with SessionLocal() as db:
        row = db.query(ReadingApproval).filter_by(language=args.language, surface=key_of(args.surface), kind=args.kind, sense="").one_or_none()
        if row is None:
            row = ReadingApproval(language=args.language, surface=key_of(args.surface), kind=args.kind, sense="", source="owner")
            db.add(row)
        row.latin = args.latin
        db.commit()
        print(f"{args.language} {row.surface} -> {row.latin} ({row.kind})")


if __name__ == "__main__":
    main()
