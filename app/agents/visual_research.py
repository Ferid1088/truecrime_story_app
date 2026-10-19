"""The VisualResearchAgent agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.visuals.research."""

from __future__ import annotations

import json

import httpx
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, Source, VisualAsset, VisualLead
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T
from app.documentary.visuals.research import (
    _KIND_ORDER,
    RIGHTS_KNOWN_PROVIDERS,
    BraveImageProvider,
    Candidate,
    LeadSourceProvider,
    SourcePageProvider,
    WikimediaProvider,
    brave_backend,
    date_span,
    footage_queries,
    names_entity,
    next_asset_code,
    query_kind,
    source_candidate_has_relevance,
    store_video_links,
)


class VisualResearchAgent:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.cfg = ai_config.visual_search
        self._client = client

    def _new_client(self) -> httpx.AsyncClient:
        from app.core.provider_guard import guard_real_provider
        guard_real_provider("image search", "construct an HTTP client")
        return httpx.AsyncClient(
            timeout=self.cfg.request_timeout_s, follow_redirects=True,
            headers={"User-Agent": self.cfg.user_agent})

    def asset_budget(self, existing: list[VisualAsset]) -> int:
        """Assets still allowed: rejected pictures are kept but take no slot."""
        return self.cfg.max_assets - sum(a.verification_status != "rejected" for a in existing)

    async def _category_candidates(self, wiki: WikimediaProvider,
                                   queries: list[dict]) -> list[Candidate]:
        """The Commons category files of the entities named by the exact
        queries (one lookup per entity name, the first query of each)."""
        out, seen = [], set()
        for q in queries:
            key = q.get("entity")
            if query_kind(q) != "exact" or not key or key in seen:
                continue
            if len(seen) >= self.cfg.max_category_entities:
                break
            seen.add(key)
            for c in await wiki.category_files(q["query"], self.cfg.category_max_files):
                c.entities = list(q.get("entities") or [key])
                c.entity_key = key
                c.entity_type = q.get("entity_type")
                c.query_kind = "exact"
                out.append(c)
        return out

    async def run(self, db: Session, case: Case, queries: list[dict],
                  progress=None, found_during: str = "research") -> dict:
        """queries: [{"query": str, "entity": key, "entities": [...],
        "entity_type": person|place|building|object|vehicle|event|document|
        organization, "kind": "exact"|"context", "footage": bool}] (all but
        "query" optional). found_during: research | production_search.
        Returns a summary; assets are committed as they arrive."""
        client = self._client or self._new_client()
        stats = {"candidates": 0, "added": 0, "duplicates": 0, "rejected": 0,
                 "errors": 0, "relevance_rejected": 0, "by_provider": {}}
        try:
            existing = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
            hashes = [a.phash for a in existing if a.phash and a.asset_type != "video"]
            urls = {a.source_url for a in existing}
            budget = self.asset_budget(existing)
            candidates: list[Candidate] = []
            # The case's own sources are case-level, not per query: read
            # them in the research pass when exact material is wanted (a
            # context-only search never touches them).
            exact_wanted = not queries or any(query_kind(q) == "exact" for q in queries)
            if ("source_pages" in self.cfg.providers and exact_wanted
                    and found_during == "research"):
                sp = SourcePageProvider(client)
                sources = (db.query(Source).filter(Source.case_id == case.id)
                           .order_by(Source.reliability_score.desc()).all())
                for s in sources[: self.cfg.max_source_pages]:
                    for c in await sp.images_for(s):
                        c.query_kind = "source"
                        candidates.append(c)
            backend = brave_backend() if any(
                p in self.cfg.providers for p in ("brave_images", "lead_sources", "brave_video_links")
            ) else None
            if ("lead_sources" in self.cfg.providers and backend and exact_wanted
                    and found_during == "research"):
                lp = LeadSourceProvider(client, backend)
                leads = (db.query(VisualLead).filter(
                    VisualLead.case_id == case.id, VisualLead.original_owner.isnot(None))
                    .order_by(VisualLead.id).limit(self.cfg.max_lead_sources).all())
                for lead in leads:
                    candidates.extend(await lp.images_for(lead, case))
            searchers = []
            if "wikimedia" in self.cfg.providers:
                searchers.append(WikimediaProvider(client))
            if "brave_images" in self.cfg.providers and backend:
                searchers.append(BraveImageProvider(backend))
            if "wikimedia" in self.cfg.providers and self.cfg.wikimedia_categories:
                candidates.extend(await self._category_candidates(
                    WikimediaProvider(client), queries))
            for q in queries[: self.cfg.max_queries]:
                kind = query_kind(q)
                ents = list(q.get("entities") or [])
                if q.get("entity") and q["entity"] not in ents:
                    ents.append(q["entity"])
                for prov in searchers:
                    if kind == "context" and prov.name not in RIGHTS_KNOWN_PROVIDERS:
                        continue
                    for c in await prov.search(q["query"], self.cfg.max_candidates_per_query):
                        if q.get("must_name") and not names_entity(c, q["must_name"]):
                            stats["rejected"] += 1
                            continue
                        c.entities = list(ents)
                        c.entity_key = q.get("entity")
                        c.entity_type = q.get("entity_type")
                        c.query_kind = kind
                        candidates.append(c)
            candidates.sort(key=lambda c: _KIND_ORDER.get(c.query_kind or "exact", 1))
            stats["candidates"] = len(candidates)
            for i, c in enumerate(candidates):
                if budget <= 0:
                    break
                if progress:
                    progress(i / max(len(candidates), 1), f"{c.provider}: {c.title or c.url}"[:120])
                if c.url in urls:
                    stats["duplicates"] += 1
                    continue
                if c.query_kind == "source" and not source_candidate_has_relevance(c, case):
                    stats["rejected"] += 1
                    stats["relevance_rejected"] += 1
                    continue
                urls.add(c.url)
                status, reason = R.classify(c.provider, c.license, c.url, c.page_url)
                if status == "do_not_use":
                    stats["rejected"] += 1
                    continue
                try:
                    data = await IM.download_image(c.url, client)
                    img = IM.open_image(data)
                except IM.ImageError:
                    stats["errors"] += 1
                    continue
                if img.width < self.cfg.min_width or img.height < 300:
                    stats["rejected"] += 1
                    continue
                ph = IM.dhash(img)
                if any(IM.hamming(ph, h) <= 6 for h in hashes):
                    stats["duplicates"] += 1
                    continue
                hashes.append(ph)
                code = next_asset_code(db)
                master = IM.save_master(img, storage.visuals_dir(case.id, c.asset_type) / f"{code}.jpg")
                thumb = IM.save_thumbnail(img, storage.thumbs_dir(case.id) / f"{code}.jpg")
                role = "context" if c.provider == "wikimedia" else "evidence"
                tier = T.provisional_tier(c.provider, role, c.entity_type, c.query_kind)
                asset = VisualAsset(
                    case_id=case.id, asset_code=code, asset_type=c.asset_type,
                    title=c.title, caption=c.caption, description=None,
                    entities_json=json.dumps(c.entities, ensure_ascii=False),
                    provider=c.provider, source_url=c.url, page_url=c.page_url,
                    source_name=c.source_name, found_for=c.found_for,
                    license=c.license, credit=c.credit,
                    date_start=date_span(c.date)[0], date_end=date_span(c.date)[1],
                    rights_status=status, rights_reason=reason,
                    local_path=storage.rel(master), thumbnail_path=storage.rel(thumb),
                    width=img.width, height=img.height, phash=ph, sha256=IM.sha256(data),
                    asset_role=role, relevance_tier=tier, case_relevance=T.tier_label(tier),
                    entity_key=c.entity_key, entity_type=c.entity_type,
                    found_during=found_during,
                )
                db.add(asset)
                db.commit()
                budget -= 1
                stats["added"] += 1
                stats["by_provider"][c.provider] = stats["by_provider"].get(c.provider, 0) + 1
            if "brave_video_links" in self.cfg.providers and backend and queries:
                stats["video_links"] = await store_video_links(
                    db, case, backend, footage_queries(queries, self.cfg.max_video_link_queries))
            fcfg = ai_config.footage
            fq = footage_queries(queries, fcfg.max_queries) if (
                fcfg.enabled and fcfg.providers and budget > 0) else []
            if fq:
                from app.documentary.visuals.footage import FootageAgent

                fs = await FootageAgent(client).run(db, case, fq, found_during, progress, budget)
                stats["footage"] = fs
                stats["added"] += fs["added"]
                for k, n in fs["by_provider"].items():
                    stats["by_provider"][k] = stats["by_provider"].get(k, 0) + n
        finally:
            if self._client is None:
                await client.aclose()
        return stats
