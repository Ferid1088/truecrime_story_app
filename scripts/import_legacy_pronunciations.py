"""Imports the earlier pronunciation tables into the pronunciation memory as legacy_unverified entries (they wait for the owner's review):
python scripts/import_legacy_pronunciations.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.documentary.pronunciation_import import import_legacy  # noqa: E402


def main() -> None:
    with SessionLocal() as db:
        print(import_legacy(db))


if __name__ == "__main__":
    main()
