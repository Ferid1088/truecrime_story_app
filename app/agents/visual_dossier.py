"""The VisualDossierAgent agent (P1): its model call, prompt and output checks run here; the
domain rules (cleaning, merging, the "NOT this" veto) stay in app.documentary.visuals.dossier."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.core.provider_guard import guard_real_provider
from app.db.models import Case, EntityDossier
from app.documentary.visuals import era as ERA
from app.documentary.visuals import identifiers as ID
from app.documentary.visuals.dossier import clean_dossier, upsert
from app.documentary.visuals.planner import _evidence
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

DOSSIER_SYSTEM = prompt("documentary/visuals/dossier/dossier_system")


class VisualDossierAgent:
    def __init__(self, client=None):
        self.gen = get_generation_provider()
        self._client = client

    async def ensure(self, db: Session, case: Case, requirements: dict) -> dict:
        """Builds a dossier for every requirement entity that does not have one yet (one model
        call for the whole missing set); entities already dossiered are left untouched (a
        dossier is read-only once written — re-run only replaces a stale one explicitly)."""
        entities = requirements.get("entities") or []
        if not entities:
            return {"skipped": True, "reason": "no entities"}
        have = {d.entity_key for d in
               db.query(EntityDossier).filter(EntityDossier.case_id == case.id).all()}
        missing = [e for e in entities if e.get("key") not in have]
        if not missing:
            return {"generated": 0, "already_had": len(have)}
        pack, _sources = _evidence(db, case.id)
        payload = {
            "case": case.canonical_title,
            "entities": [{"key": e["key"], "name": e["name"], "type": e.get("type"),
                         "period": e.get("period"), "aliases": e.get("aliases") or []}
                        for e in missing],
            "facts": [{"id": f["id"], "claim": f["claim"]} for f in pack["facts"]],
        }
        with track_run(db, case.id, "Visual Entity Dossier",
                       input_summary=f"entities={len(missing)}") as run:
            raw, res = await run_agent("visuals.dossier", self.gen, json.dumps(payload, ensure_ascii=False),
                                       system=DOSSIER_SYSTEM)
            stamp_run(run, res, "visual_entity_dossier")
        era = ERA.era_of(case)
        known_fact_ids = {f["id"] for f in pack["facts"]}
        by_key = {e["key"]: e for e in missing}
        written = 0
        for entry in (raw.get("dossiers") if isinstance(raw, dict) else None) or []:
            raw_key = re.sub(r"[^a-z0-9_]+", "_", str((entry or {}).get("entity") or "").lower()).strip("_")
            entity = by_key.get(raw_key)
            if entity is None:
                continue
            cleaned = clean_dossier(entry, entity, known_fact_ids, era)
            if cleaned is None:
                continue
            cleaned["wikidata_id"], cleaned["commons_category"] = await self._identifiers(
                cleaned["canonical_name"])
            upsert(db, case.id, cleaned, getattr(res, "model", None))
            written += 1
        return {"generated": written, "requested": len(missing), "already_had": len(have)}

    async def _identifiers(self, name: str) -> tuple[str | None, str | None]:
        """Real identifiers fetched from the official APIs (never a model guess)."""
        client = self._client
        if client is None:
            guard_real_provider("entity identifiers", "look up Wikidata/Commons")
            import httpx

            async with httpx.AsyncClient(timeout=ai_config.visual_search.request_timeout_s) as c:
                return await ID.wikidata_id(c, name), await ID.commons_category(c, name)
        return await ID.wikidata_id(client, name), await ID.commons_category(client, name)
