"""Opening strategies: how a film begins is chosen per case.

The Story Director picks one strategy from `ai_config.opening.strategies`
(follow-up films always open with the earlier coverage) and stores it in
the master's narrative_structure as `opening_strategy` + `opening_reason`.
Every later stage (visual director, production script, video record)
reads it from there — and the director avoids the strategies of the
most recent films so the channel does not open every film the same way.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import EditorialBlueprint, StoryVersion


def strategies() -> dict[str, str]:
    return dict(ai_config.opening.strategies)


def _structure(version: StoryVersion | None) -> dict:
    if version is None:
        return {}
    try:
        data = json.loads(version.narrative_structure or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def master_of(db: Session, version: StoryVersion | None) -> StoryVersion | None:
    """The master a version is told from (a master is its own)."""
    if version is not None and version.kind == "spoken" and version.master_version_id:
        return db.get(StoryVersion, version.master_version_id) or version
    return version


def opening_of_version(db: Session, version: StoryVersion | None) -> dict:
    """{"strategy", "reason"} of the film a version belongs to (empty
    strategy for stories written before openings were chosen)."""
    s = _structure(master_of(db, version))
    return {"strategy": s.get("opening_strategy"), "reason": s.get("opening_reason")}


def opening_of_blueprint(db: Session, blueprint: EditorialBlueprint | None) -> dict:
    if blueprint is None:
        return {"strategy": None, "reason": None}
    return opening_of_version(db, db.get(StoryVersion, blueprint.story_version_id))


def recent_openings(db: Session, n: int | None = None, exclude_case_id: int | None = None
                    ) -> list[str]:
    """Opening strategies of the most recent films (newest first; one per
    case — the newest master of each case)."""
    n = ai_config.opening.avoid_recent if n is None else n
    if n <= 0:
        return []
    out: list[str] = []
    seen: set[int] = set()
    rows = (db.query(StoryVersion).filter(StoryVersion.kind == "master")
            .order_by(StoryVersion.id.desc()).limit(200).all())
    for v in rows:
        if v.case_id in seen or v.case_id == exclude_case_id:
            continue
        strategy = _structure(v).get("opening_strategy")
        if not strategy:
            continue
        seen.add(v.case_id)
        out.append(strategy)
        if len(out) >= n:
            break
    return out


def normalize_strategy(value: str | None, follow_up: bool = False) -> str | None:
    """A known strategy name or None. Follow-ups always open with the
    earlier coverage."""
    if follow_up:
        return ai_config.opening.follow_up_strategy
    v = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return v if v in ai_config.opening.strategies else None
