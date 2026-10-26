"""Entity dossiers (P1): the stored reference per important element of the case (person, place,
building, object, event, document, vehicle) in `entity_dossiers` (migration 0060). Read-only here:
merge_into_entities() enriches the planner's entities with aliases (including local-language
spellings), the era, a visual description and the "NOT this" list of name collisions, which feed
the research queries and the verifier's entity list (app.documentary.mvp.verify.entity_view).
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, EntityDossier
from app.documentary.visuals import era as ERA


def _limits():
    return ai_config.entity_dossier


def _strings(values, limit: int, max_chars: int = 120) -> list[str]:
    out: list[str] = []
    for v in values if isinstance(values, list) else []:
        s = " ".join(str(v).split())[:max_chars]
        if s and s.lower() not in {x.lower() for x in out}:
            out.append(s)
    return out[:limit]


def merge_into_entities(db: Session, case: Case, entities: list[dict]) -> list[dict]:
    """Enriches the planner's entity dicts with their stored dossier (when one exists) and
    always stamps the case's era (P3/P4) and date_window (the archive providers' era filter,
    open_data_archives) — even before any dossier has been generated, so query phrasing
    degrades gracefully rather than requiring the dossier stage to have run first."""
    rows = {d.entity_key: d for d in
            db.query(EntityDossier).filter(EntityDossier.case_id == case.id).all()}
    case_era = ERA.era_of(case)
    window = list(ERA.date_window(case) or ())
    out = []
    for e in entities:
        merged = dict(e)
        d = rows.get(e.get("key") or "")
        if d is None:
            merged.setdefault("era", case_era)
            merged.setdefault("not_this", [])
            merged.setdefault("date_window", window)
            out.append(merged)
            continue
        merged["era"] = d.era
        # case-level, not per dossier row: the year window the archive date filters use
        merged["date_window"] = window
        merged["aliases"] = _strings(
            list(merged.get("aliases") or []) + json.loads(d.aliases_json or "[]"),
            _limits().max_aliases, 80)
        merged["visual_description"] = d.visual_description
        merged["not_this"] = json.loads(d.not_this_json or "[]")
        merged["wikidata_id"] = d.wikidata_id
        merged["commons_category"] = d.commons_category
        merged["likely_sources"] = json.loads(d.likely_sources_json or "[]")
        merged["expected_captions"] = json.loads(d.expected_captions_json or "[]")
        merged["negative_terms"] = json.loads(d.negative_terms_json or "[]")
        out.append(merged)
    return out
