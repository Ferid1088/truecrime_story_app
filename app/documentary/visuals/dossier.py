"""Entity dossiers (P1): one JSON per important element of the case (person, place, building,
object, event, document, vehicle), written once by a model from the case facts and stored in
`entity_dossiers` (migration 0060). A read-only reference, used in four places:
  * query generation    — merge_into_entities() enriches the planner's aliases with the
                           dossier's (including local-language spellings) before research_queries
                           / expansion_queries run;
  * the pre-filter       — the same enriched `aliases` feed `must_name` (planner.expansion_item);
  * the verifier's input — merge_into_entities() is read by app.agents.visual_verification._payload
                           to add `description`/`not_this` to each entity_list item;
  * candidate ranking     — app.documentary.visuals.director.entity_score vetoes a text match
                           against a "NOT this" phrase (a name collision or look-alike).
"""

from __future__ import annotations

import json
import re

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


def clean_dossier(raw: dict, entity: dict, known_fact_ids: set[str], era: str) -> dict | None:
    """One validated dossier entry for `entity`, or None when the model's answer is not a dict
    or names a different entity than the one asked about — a dossier is never invented for
    something the planner never asked for, and never attached to the wrong entity key."""
    if not isinstance(raw, dict):
        return None
    key = re.sub(r"[^a-z0-9_]+", "_", str(raw.get("entity") or "").lower()).strip("_")
    if not key or key != entity.get("key"):
        return None
    limits = _limits()
    supports = [f for f in (raw.get("supports_fact_ids") or [])
               if f in known_fact_ids][:limits.max_fact_ids]
    aliases = _strings(list(raw.get("aliases") or []) + list(entity.get("aliases") or []),
                       limits.max_aliases, 80)
    description = str(raw.get("visual_description"))[:limits.max_description_chars] \
        if raw.get("visual_description") else None
    return {
        "entity_key": key,
        "canonical_name": str(raw.get("canonical_name") or entity.get("name") or "")[:200],
        "entity_type": entity.get("type") or "object",
        "era": era,
        "aliases": aliases,
        "visual_description": description,
        "not_this": _strings(raw.get("not_this"), limits.max_not_this, 150),
        "wikidata_id": (str(raw.get("wikidata_id"))[:20] or None) if raw.get("wikidata_id") else None,
        "commons_category": (str(raw.get("commons_category"))[:200]
                             if raw.get("commons_category") else None),
        "likely_sources": _strings(raw.get("likely_sources"), limits.max_likely_sources, 100),
        "expected_captions": _strings(raw.get("expected_captions"), limits.max_expected_captions, 150),
        "negative_terms": _strings(raw.get("negative_terms"), limits.max_negative_terms, 60),
        "supports_fact_ids": supports,
    }


def upsert(db: Session, case_id: int, cleaned: dict, generation_model: str | None) -> EntityDossier:
    """Write once; a re-run of the dossier stage updates the same row in place (the dossier is
    read-only to everything else — only the stage that built it may change it)."""
    row = (db.query(EntityDossier)
          .filter(EntityDossier.case_id == case_id, EntityDossier.entity_key == cleaned["entity_key"])
          .first())
    if row is None:
        row = EntityDossier(case_id=case_id, entity_key=cleaned["entity_key"])
        db.add(row)
    row.canonical_name = cleaned["canonical_name"]
    row.entity_type = cleaned["entity_type"]
    row.era = cleaned["era"]
    row.aliases_json = json.dumps(cleaned["aliases"], ensure_ascii=False)
    row.visual_description = cleaned["visual_description"]
    row.not_this_json = json.dumps(cleaned["not_this"], ensure_ascii=False)
    row.wikidata_id = cleaned["wikidata_id"]
    row.commons_category = cleaned["commons_category"]
    row.likely_sources_json = json.dumps(cleaned["likely_sources"], ensure_ascii=False)
    row.expected_captions_json = json.dumps(cleaned["expected_captions"], ensure_ascii=False)
    row.negative_terms_json = json.dumps(cleaned["negative_terms"], ensure_ascii=False)
    row.supports_fact_ids_json = json.dumps(cleaned["supports_fact_ids"], ensure_ascii=False)
    row.generation_model = generation_model
    db.commit()
    db.refresh(row)
    return row


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


def __getattr__(name: str):
    # the agent class lives in app/agents/visual_dossier.py (imported when first asked for)
    if name == "VisualDossierAgent":
        from app.agents.visual_dossier import VisualDossierAgent

        return VisualDossierAgent
    raise AttributeError(name)
