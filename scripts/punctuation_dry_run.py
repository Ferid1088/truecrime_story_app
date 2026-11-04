"""The punctuation stage over a version's narration blocks without any paid call (the deterministic rules; the agents stay off whatever the
config says): the version's source identity and checksum before and after, the statistics, every layer checked against the approved text, and the
record written to --out. Nothing is voiced and nothing is stored in the database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import StoryVersion  # noqa: E402
from app.documentary.performance import performance_for_version  # noqa: E402
from app.documentary.punctuation import stage as PS  # noqa: E402
from app.documentary.punctuation.layer import check  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    with SessionLocal() as db:
        version = db.get(StoryVersion, args.version)
        before = hashlib.sha256((version.story_text or "").encode()).hexdigest()
        spent = BudgetService(db).snapshot(version.case_id).spent_eur
        plan = performance_for_version(db, version)
        results, stats = await PS.for_version(db, version.case_id, version, plan, PS.PunctuationStage(None, None, use_agents=False))
        db.refresh(version)
        after = hashlib.sha256((version.story_text or "").encode()).hexdigest()
        violations = {b: check(r.layer) for b, r in results.items() if check(r.layer)}
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        PS.save(out, results, stats)
        report = {"version": version.id, "kind": version.kind, "language": version.language, "story_sha256_before": before, "story_sha256_after": after,
                  "story_unchanged": before == after, "statistics": stats, "layer_violations": violations,
                  "spent_eur_during_run": round(BudgetService(db).snapshot(version.case_id).spent_eur - spent, 6), "record": str(out)}
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
