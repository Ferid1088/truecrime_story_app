"""Puts the imported pronunciation entries that the app approved by itself back to legacy_unverified (a person's approvals are not touched):
python scripts/revert_auto_approvals.py [--language fa]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import PronunciationEntry  # noqa: E402
from app.documentary.pronunciation_memory import PronunciationMemory  # noqa: E402

STATUSES = ("auto_approved", "auto_verified", "unresolved")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--language", default="fa")
    args = ap.parse_args()
    with SessionLocal() as db:
        memory = PronunciationMemory(db)
        ids = [e.id for e in db.query(PronunciationEntry).filter(PronunciationEntry.language == args.language, PronunciationEntry.status.in_(STATUSES))]
        for i in ids:
            memory.revert_to_legacy(i, reason="the app may not approve imported entries until the phonetic recognizer has passed an independent evaluation")
        print(f"{len(ids)} entries put back to legacy_unverified")


if __name__ == "__main__":
    main()
