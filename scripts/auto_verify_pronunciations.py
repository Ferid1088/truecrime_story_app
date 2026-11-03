"""The app verifies the pronunciation entries itself (up to three attempts each; no agreement leaves an entry unresolved). Refuses while
config pronunciation_auto.enabled is false: python scripts/auto_verify_pronunciations.py [--language fa] [--version 289] [--limit N]. Calls the language models; changes only the pronunciation memory."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.documentary.pronunciation_auto import verify_all  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--language", default="fa")
    ap.add_argument("--version", type=int, default=289)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    with SessionLocal() as db:
        results = await verify_all(db, args.language, db.get(StoryVersion, args.version), limit=args.limit)
    agreed = sum(1 for r in results if r.agreed)
    print(f"{len(results)} entries: {agreed} verified, {len(results) - agreed} unresolved after {sorted({r.attempts for r in results if not r.agreed})} attempts")
    for r in results:
        print(r.entry_id, r.surface, "verified" if r.agreed else "accepted", r.attempts, "|", r.notes[-1][:120])


if __name__ == "__main__":
    asyncio.run(main())
