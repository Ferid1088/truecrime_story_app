"""Runs a version's reading stage as the film pipeline does (BILLABLE: model calls for the film table, the editor, the blind checker and the arbiter of
every block whose input or prompts changed), after copying the stored reading file aside. Every call is checked against the case budget and booked.
Prints what it cost and how many words are left in doubt for the owner (they are answered in the Reading Review tab; nothing is voiced here)."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import SessionLocal  # noqa: E402
from app.db.models import Case, StoryVersion  # noqa: E402
from app.documentary.reading import stage as RS  # noqa: E402
from app.documentary.reading.text import ReadingStage  # noqa: E402
from app.services.budget import BudgetService  # noqa: E402
from app.utils import utc_now  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--language", default="fa")
    p.add_argument("--confirm-spend", action="store_true")
    args = p.parse_args()
    if not args.confirm_spend:
        raise SystemExit("billable: needs --confirm-spend")
    with SessionLocal() as db:
        version = db.get(StoryVersion, args.version)
        case = db.get(Case, version.case_id)
        before = BudgetService(db).snapshot(case.id)
        if before.spent_eur >= before.cap_eur:
            raise SystemExit(f"the case has spent {before.spent_eur} of {before.cap_eur} EUR")
        stage = ReadingStage(db, case.id, version, args.language, [])
        if stage.path.exists():
            backup = stage.path.with_name(f"{stage.path.stem}.backup-{utc_now().strftime('%Y%m%dT%H%M%S')}.json")
            shutil.copy2(stage.path, backup)
            print(f"stored readings copied to {backup}", flush=True)
        items, read = await RS.result(db, case, version, args.language)
        after = BudgetService(db).snapshot(case.id)
    print(json.dumps({"blocks": len(items), "words": sum(len(i.words) for i in items), "open_doubts": read.open_doubts,
                      "changed_by_code": sum(len(n) for n in read.notes.values()), "spent_eur": round(after.spent_eur - before.spent_eur, 4),
                      "case_spent_eur": after.spent_eur, "case_cap_eur": after.cap_eur}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
