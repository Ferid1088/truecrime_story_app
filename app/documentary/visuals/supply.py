"""Supply of real pictures: a film of N seconds needs about
N / supply.seconds_per_real_picture verified real photos, documents or video
pieces. When the first research and check fall short, up to
supply.max_searches - 1 follow-up searches (the next query slices, never the
same query twice) run, each followed by a vision check of its new candidates.
The loop stops when the target is reached, a search adds nothing, or the
searches are used up; illustrations stay the labelled fallback."""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary.visuals.planner import supply_queries


def target(film_seconds: float) -> int:
    return math.ceil(film_seconds / ai_config.supply.seconds_per_real_picture)


def verified_real(db: Session, case: Case) -> int:
    """Verified pictures and video pieces that are not generated."""
    return (db.query(VisualAsset).filter(
        VisualAsset.case_id == case.id, VisualAsset.verification_status == "verified",
        VisualAsset.provider != "generated").count())


async def top_up(db: Session, case: Case, requirements: dict, beat_ids: list[str] | None,
                 film_seconds: float, research, verify) -> dict:
    """research(queries) -> stats with "added"; verify() -> stats. Returns the
    supply report: target, verified count before/after, searches run."""
    need = target(film_seconds)
    before = verified_real(db, case)
    report: dict[str, Any] = {"target": need, "verified_before": before, "searches": 1, "rounds": []}
    have = before
    for round_index in range(1, ai_config.supply.max_searches):
        if have >= need:
            break
        queries = supply_queries(requirements, beat_ids, round_index)
        if not queries:
            break
        stats = await research(queries)
        report["searches"] += 1
        checked = await verify() if stats.get("added") else {"checked": 0}
        have = verified_real(db, case)
        report["rounds"].append({"queries": len(queries), "added": stats.get("added", 0),
                                 "checked": checked.get("checked", 0), "verified": have})
        if not stats.get("added"):
            break
    report["verified_after"] = have
    report["reached"] = have >= need
    return report
