"""One fresh Persian spoken version from the approved Persian final story (the only source), with the real agents; prints what it cost."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ai_config import ai_config  # noqa: E402
from app.agents.stages import get_stage_agent  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.db.models import Case, StoryVersion  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--case", type=int, required=True)
    p.add_argument("--final", type=int, required=True)
    p.add_argument("--sha256", required=True)
    p.add_argument("--beats-per-call", type=int, default=0, help="this run only: writer calls of N beats instead of the whole story (0 = config)")
    p.add_argument("--resume-dir", help="keeps each finished model answer here, so a rerun after a provider error pays only for what is missing")
    args = p.parse_args()
    if args.beats_per_call:
        ai_config.spoken.beats_per_call = args.beats_per_call
    resume = Path(args.resume_dir) if args.resume_dir else None
    with SessionLocal() as db:
        master = db.get(StoryVersion, args.final)
        sha = hashlib.sha256(master.story_text.encode()).hexdigest()
        if sha != args.sha256 or master.language != "fa" or master.kind != "final":
            raise SystemExit(f"source {args.final} does not match the pinned Persian final ({sha})")
        before = BudgetService(db).snapshot(args.case).spent_eur
        version = await get_stage_agent("spoken", resume_dir=resume).create(db, db.get(Case, args.case), master, "fa")
        after = BudgetService(db).snapshot(args.case).spent_eur
        db.refresh(master)
        print(json.dumps({"spoken_version": version.id, "status": version.status, "words": len(version.story_text.split()),
                          "source_unchanged": hashlib.sha256(master.story_text.encode()).hexdigest() == sha,
                          "cost_eur": round(after - before, 4), "spent_eur_total": round(after, 4)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
