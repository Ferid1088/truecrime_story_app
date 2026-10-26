"""Case-level picture and related-video search: runs right after a case's
research, before any blueprint exists, so a researched case always has stored
candidates (links and rights status as the beat-level search stores them)."""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, Fact, MediaLink, VisualAsset
from app.documentary.mvp.research import query_item


def case_queries(db: Session, case: Case) -> list[dict]:
    """The case title, then the people and places the facts name most."""
    cfg = ai_config.visual_search
    title = case.canonical_title
    queries = [query_item({"key": title, "type": "event", "footage": True}, title),
               query_item({"key": title, "type": "event", "footage": True}, f"{title} photograph")]
    for kind, column in (("person", "people_json"), ("place", "locations_json")):
        counts: Counter = Counter()
        for (raw,) in db.query(getattr(Fact, column)).filter(Fact.case_id == case.id):
            counts.update(n for n in json.loads(raw or "[]") if n)
        for name, _ in counts.most_common(cfg.case_level_entities):
            queries.append(query_item({"key": name, "type": kind, "footage": kind == "person"},
                                      f"{name} {title}"))
    return queries


def has_stored_candidates(db: Session, case: Case) -> bool:
    return bool(db.query(VisualAsset.id).filter(VisualAsset.case_id == case.id).first()
                or db.query(MediaLink.id).filter(MediaLink.case_id == case.id).first())


async def search_case_visuals(db: Session, case: Case, agent=None) -> dict | None:
    """Search once for a case that has no stored picture or related video;
    returns the search stats, or None when the case already has candidates."""
    if has_stored_candidates(db, case):
        return None
    from app.documentary.visuals.research import VisualResearchAgent

    return await (agent or VisualResearchAgent()).run(
        db, case, case_queries(db, case), found_during="research")
