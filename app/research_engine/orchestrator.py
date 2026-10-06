"""The research loop (Parts 6, 37-39, 41-43).

PLAN -> SEARCH -> RANK -> FETCH -> EXTRACT -> ANALYZE NOVELTY ->
IDENTIFY GAPS -> FOLLOW-UP -> STOP.

LLMs plan and analyze; they never see raw search-result dumps — only
selected retrieved content. Every acceptance/rejection is recorded so
the run replays deterministically in the query inspector.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from app.research_engine.dedupe import SourceFamilyDetector
from app.research_engine.embeddings import cosine_matrix
from app.research_engine.extractor import extract
from app.research_engine.fetcher import DocumentFetcher
from app.research_engine.language import detect_source_language
from app.research_engine.planner import (
    GapAnalyzer,
    ResearchQueryPlanner,
    ResearchAssembler,
    ResultReranker,
)
from app.research_engine.ranking import (
    needs_llm_rerank,
    rank_results,
    select_for_fetch,
)
from app.research_engine.search import SearchBackendRegistry
from app.research_engine.source_quality import (
    TYPE_TO_SOURCE_TYPE,
    classify_source,
)
from app.research_engine.urlnorm import canonicalize_url, domain_of

log = logging.getLogger(__name__)

_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


@dataclass
class EngineConfig:
    """Runtime knobs — populated from ai_config.search_engine."""
    max_results_per_query: int = 10
    max_queries_per_language: int = 8
    max_rounds_per_language: int = 3
    max_fetches_per_language: int = 12
    min_full_text_chars: int = 1200
    min_accept_score: float = 0.2
    rerank_band: tuple[float, float] = (0.35, 0.6)
    max_rerank_candidates: int = 10
    enable_llm_rerank: bool = True
    enable_gap_analysis: bool = True
    consecutive_low_novelty_stop: int = 2
    max_duration_s: float = 600.0
    fetch_quality_floor: float = 0.15


@dataclass
class Progress:
    """Live stage/meta sink shared with the provider's job registry."""
    stage_cb: object = None
    meta_cb: object = None

    def stage(self, s: str):
        if self.stage_cb:
            self.stage_cb(s)

    def meta(self, **kw):
        if self.meta_cb:
            self.meta_cb(**kw)


def _case_terms(case_context: dict) -> set[str]:
    toks: set[str] = set()
    for part in [case_context.get("title") or "",
                 " ".join(case_context.get("key_people") or []),
                 case_context.get("location") or "",
                 (case_context.get("summary") or "")[:500]]:
        toks |= {w.lower() for w in _WORD.findall(part) if len(w) > 3}
    return toks


class ResearchOrchestrator:
    def __init__(
        self,
        registry: SearchBackendRegistry,
        fetcher: DocumentFetcher,
        generation_provider=None,
        embedder=None,
        config: EngineConfig | None = None,
    ):
        self.registry = registry
        self.fetcher = fetcher
        self.gen = generation_provider
        self.embedder = embedder
        self.cfg = config or EngineConfig()
        self.planner = None
        self.reranker = None
        self.gaps = None
        self.assembler = None
        self._corpus_vecs = None

    def _bind_llm(self, tel: dict):
        """Bind per-run telemetry sinks to the intelligence helpers —
        an orchestrator instance may serve many concurrent jobs."""
        if not self.gen:
            return
        self.planner = ResearchQueryPlanner(self.gen, tel)
        self.reranker = ResultReranker(self.gen, tel)
        self.gaps = GapAnalyzer(self.gen, tel)
        self.assembler = ResearchAssembler(self.gen, tel)

    # ------------------------------------------------------------------
    # Deep research for one case
    # ------------------------------------------------------------------

    async def research_case(
        self,
        case_context: dict,
        languages: list[str],
        objective: str | None = None,
        corpus: dict | None = None,
        progress: Progress | None = None,
    ) -> dict:
        progress = progress or Progress()
        corpus = corpus or {}
        seen_canonical: set[str] = set(corpus.get("canonical_urls") or [])
        tel = _telemetry()
        self._bind_llm(tel)
        queries_out: list[dict] = []
        results_out: list[dict] = []
        sources_out: list[dict] = []
        language_stats: dict[str, dict] = {}
        all_gaps: list[str] = list(case_context.get("research_gaps") or [])
        contradictions: list[str] = []
        started = time.monotonic()

        families = SourceFamilyDetector()
        for entry in corpus.get("family_corpus") or []:
            families.add_source(entry.get("source_id") or 0,
                                entry.get("text") or "",
                                entry.get("family"))

        # Dense corpus similarity for novelty scoring (Parts 18/23/25):
        # embed existing corpus texts once per run; cached embeddings
        # make repeat runs near-free.
        self._corpus_vecs = None
        if self.embedder and self.embedder.is_configured():
            ctexts = [(e.get("text") or "")[:600]
                      for e in (corpus.get("family_corpus") or [])][:40]
            ctexts = [t for t in ctexts if t.strip()]
            if ctexts:
                try:
                    self._corpus_vecs = await self.embedder.embed(ctexts)
                except Exception as e:  # noqa: BLE001
                    log.warning("corpus embedding failed: %s", e)

        for lang in languages:
            if time.monotonic() - started > self.cfg.max_duration_s:
                language_stats[lang] = {"stop_reason": "max_duration"}
                break
            stats = await self._research_language(
                lang, case_context, objective, seen_canonical,
                families, tel, queries_out, results_out, sources_out,
                all_gaps, contradictions, progress, started)
            language_stats[lang] = stats

        # Assembler: one final case-level summary (cheap).
        progress.stage("assembling")
        case_summary = {}
        if self.assembler and sources_out:
            case_summary = await self.assembler.summarize(
                case_context, sources_out)

        if self.embedder:
            tel["embedding_tokens"] = self.embedder.total_tokens
            tel["embedding_cost_usd"] = self.embedder.total_cost_usd

        return {
            "queries": queries_out,
            "results": results_out,
            "sources": sources_out,
            "case": case_summary,
            "research_gaps": all_gaps[:30],
            "possible_contradictions": contradictions[:30],
            "language_stats": language_stats,
            "_telemetry": tel,
        }

    async def _research_language(
        self, lang: str, case_context: dict, objective: str | None,
        seen_canonical: set[str], families: SourceFamilyDetector,
        tel: dict, queries_out: list, results_out: list,
        sources_out: list, all_gaps: list, contradictions: list,
        progress: Progress, started: float,
    ) -> dict:
        stats = {
            "queries": 0, "search_calls": 0, "fetch_calls": 0,
            "sources_found": 0, "sources_accepted": 0,
            "full_text": 0, "partial_text": 0, "summary_only": 0,
            "metadata_only": 0, "rejected": 0, "cost_usd": 0.0,
            "cache_hits": 0,
        }
        prev_queries: list[str] = []
        low_novelty_streak = 0
        fetched = 0

        for round_i in range(self.cfg.max_rounds_per_language):
            if time.monotonic() - started > self.cfg.max_duration_s:
                stats["stop_reason"] = "max_duration"
                break
            remaining_q = self.cfg.max_queries_per_language - stats["queries"]
            if remaining_q <= 0:
                stats["stop_reason"] = "max_queries"
                break

            progress.stage(f"planning:{lang}")
            planned = []
            if self.planner:
                try:
                    planned = await self.planner.plan(
                        case_context, lang,
                        research_gaps=all_gaps,
                        previous_queries=prev_queries,
                        round_index=round_i,
                        max_queries=remaining_q,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("query planning crashed for %s: %s", lang, e)
                    planned = self.planner._fallback_queries(
                        case_context, lang, round_i)
            if not planned:
                stats["stop_reason"] = stats.get("stop_reason") or "planner_empty"
                break

            new_this_round = 0
            for pq in planned:
                if stats["queries"] >= self.cfg.max_queries_per_language:
                    break
                q_idx = len(queries_out)
                queries_out.append({
                    "language": pq.language,
                    "query": pq.query,
                    "purpose": pq.purpose,
                    "priority": str(pq.priority),
                    "round": round_i,
                })
                prev_queries.append(pq.query)
                stats["queries"] += 1
                progress.stage(f"searching:{lang}")
                try:
                    raw = await self.registry.search(
                        pq.query, pq.language,
                        limit=self.cfg.max_results_per_query)
                except Exception as e:  # noqa: BLE001
                    log.warning("search failed for %r: %s", pq.query, e)
                    raw = []
                stats["search_calls"] += 1
                tel["searches"] += 1
                stats["sources_found"] += len(raw)
                new_this_round += await self._process_results(
                    raw, q_idx, lang, case_context, seen_canonical,
                    families, tel, results_out, sources_out, stats,
                    fetched_budget=lambda: self.cfg.max_fetches_per_language - stats["fetch_calls"],
                    progress=progress)
                if stats["fetch_calls"] >= self.cfg.max_fetches_per_language:
                    break

            # Gap analysis for this round (bounded LLM usage).
            if self.gaps and self.cfg.enable_gap_analysis and sources_out:
                progress.stage(f"analyzing:{lang}")
                recent = [s for s in sources_out
                          if s.get("_language_round") == round_i or s.get("_lang") == lang]
                analysis = await self.gaps.analyze(
                    case_context, recent[-12:], all_gaps)
                remaining = [g for g in (analysis.get("remaining_gaps") or [])
                             if isinstance(g, str)][:15]
                resolved = analysis.get("resolved_gaps") or []
                if remaining:
                    all_gaps[:] = remaining
                for c in (analysis.get("new_contradictions") or [])[:10]:
                    if isinstance(c, str) and c not in contradictions:
                        contradictions.append(c)
                novel = bool(analysis.get("novel_information_detected",
                                        new_this_round > 0))
                if not novel and new_this_round == 0:
                    low_novelty_streak += 1
                else:
                    low_novelty_streak = 0
                if resolved and all_gaps:
                    all_gaps[:] = [g for g in all_gaps
                                   if g not in resolved]
            else:
                low_novelty_streak = (
                    low_novelty_streak + 1 if new_this_round == 0 else 0)

            if low_novelty_streak >= self.cfg.consecutive_low_novelty_stop:
                stats["stop_reason"] = "low_novelty"
                break
        else:
            stats["stop_reason"] = stats.get("stop_reason") or "max_rounds"
        stats.setdefault("stop_reason", "completed")
        stats["cost_usd"] = round(stats["cost_usd"], 5)
        return stats

    async def _process_results(
        self, raw, q_idx, lang, case_context, seen_canonical,
        families, tel, results_out, sources_out, stats,
        fetched_budget, progress,
    ) -> int:
        """Rank -> accept/reject -> fetch top candidates -> extract."""
        similarity = None
        if self._corpus_vecs is not None and getattr(
                self._corpus_vecs, "size", 0):
            try:
                probe = [f"{r.title or ''} {r.snippet or ''}"[:600]
                         for r in raw]
                vecs = await self.embedder.embed(probe)
                sims = cosine_matrix(vecs, self._corpus_vecs)
                similarity = [float(sims[i].max()) if sims.shape[1]
                              else 0.0 for i in range(len(raw))]
            except Exception as e:  # noqa: BLE001
                log.warning("candidate embedding failed: %s", e)
        ranked = rank_results(
            raw, query=raw[0].query if raw else "",
            case_terms=_case_terms(case_context),
            seen_canonical=seen_canonical,
            similarity_to_corpus=similarity,
            min_accept_score=self.cfg.min_accept_score,
        )

        # Optional LLM rerank for the ambiguous band (bounded).
        # Cross-language suspects — results whose detected language is
        # not the case language and whose lexical tie to the case is
        # near-zero — must be LLM-verified or they are rejected, so a
        # Persian/Arabic query cannot accept unrelated pages on
        # quality+novelty score alone (Part 23 cross-language suppression).
        case_lang = (case_context.get("language") or "en")[:5].lower()
        suspect_ids = {id(r) for r in ranked
                       if self._cross_lang_suspect(r, case_lang)}
        verified_ids: set[int] = set()
        if self.reranker and self.cfg.enable_llm_rerank:
            band_ids = {id(r) for r in ranked
                        if needs_llm_rerank(r, self.cfg.rerank_band)}
            band_ids |= suspect_ids
            if band_ids:
                verdicts = await self.reranker.rerank(
                    case_context,
                    [{"idx": i, "url": r.result.url,
                      "title": r.result.title,
                      "snippet": (r.result.snippet or "")[:300]}
                     for i, r in enumerate(ranked)
                     if id(r) in band_ids]
                    [: self.cfg.max_rerank_candidates])
                for i, r in enumerate(ranked):
                    v = verdicts.get(i)
                    if v and v["fetch"]:
                        r.score = round(r.score + 0.1, 4)
                        if v["relevance"] >= 0.3:
                            verified_ids.add(id(r))
                    elif v and not v["fetch"] and v["relevance"] < 0.3:
                        r.rejected = True
                        r.rejection_reason = "llm_rejected"
                ranked.sort(key=lambda x: x.score, reverse=True)
        for r in ranked:
            if id(r) in suspect_ids and id(r) not in verified_ids \
                    and not r.rejected:
                r.rejected = True
                r.rejection_reason = "cross_language_unverified"

        fetchable = select_for_fetch(
            ranked, fetched_budget(), self.cfg.fetch_quality_floor)
        fetch_set = {id(r) for r in fetchable}
        new_sources = 0

        for rk in ranked:
            r = rk.result
            entry = {
                "query_idx": q_idx,
                "url": r.url,
                "canonical_url": _ingest_canonical(r),
                "title": r.title,
                "snippet": (r.snippet or "")[:1000] or None,
                "rank": r.rank,
                "model": None,
                "result_language": r.detected_language or lang,
                "relevance": rk.relevance,
                "credibility": rk.quality,
                "novelty": rk.novelty,
                "fetch_status": "skipped",
                "accepted": False,
                "rejection_reason": rk.rejection_reason,
            }
            r_idx = len(results_out)
            results_out.append(entry)
            if rk.rejected:
                stats["rejected"] += 1
                continue
            if id(rk) not in fetch_set:
                stats["rejected"] += 1
                entry["rejection_reason"] = entry["rejection_reason"] or "not_selected"
                continue

            # Fetch + extract.
            progress.stage(f"fetching:{lang}")
            outcome = await self.fetcher.fetch(r.url)
            tel["fetches"] += 1
            stats["fetch_calls"] += 1
            if outcome.cache_hit:
                stats["cache_hits"] += 1
                tel["cache_hits"] = tel.get("cache_hits", 0) + 1
            entry["fetch_status"] = outcome.status
            if outcome.final_url:
                entry["final_url"] = outcome.final_url
            if outcome.status != "ok" and outcome.status != "not_modified":
                entry["rejection_reason"] = (
                    f"fetch_{outcome.error or outcome.status}")
                stats["rejected"] += 1
                # Unfetchable result can still become a metadata_only
                # source (provenance preserved, nothing fabricated).
                if outcome.status == "blocked":
                    continue
                src = self._source_from_result(
                    r, r_idx, lang, outcome, None, None)
                sources_out.append(src)
                entry["accepted"] = True
                stats["sources_accepted"] += 1
                stats["metadata_only"] += 1
                new_sources += 1
                if r.canonical_url:
                    seen_canonical.add(r.canonical_url)
                    seen_canonical.add(r.canonical_url.split("?")[0])
                continue

            doc = extract(
                outcome.body or b"", outcome.content_type,
                outcome.final_url or r.url,
                min_full_chars=self.cfg.min_full_text_chars)
            entry["model"] = doc.extractor
            entry["fetch_status"] = "fetched"
            if not doc.text:
                entry["rejection_reason"] = "no_text"
                src = self._source_from_result(
                    r, r_idx, lang, outcome, doc, None)
                sources_out.append(src)
                entry["accepted"] = True
                stats["sources_accepted"] += 1
                stats["metadata_only"] += 1
                new_sources += 1
                if r.canonical_url:
                    seen_canonical.add(r.canonical_url)
                    seen_canonical.add(r.canonical_url.split("?")[0])
                continue

            src = self._source_from_result(
                r, r_idx, lang, outcome, doc, families)
            if src.get("_family_duplicate"):
                entry["rejection_reason"] = "duplicate"
                stats["rejected"] += 1
                continue
            sources_out.append(src)
            entry["accepted"] = True
            stats["sources_accepted"] += 1
            key = src.get("content_status") or "metadata_only"
            if key in stats:
                stats[key] += 1
            new_sources += 1
            if r.canonical_url:
                seen_canonical.add(r.canonical_url)
                seen_canonical.add(r.canonical_url.split("?")[0])
            if doc.text:
                families.add_source(
                    r_idx, doc.text[:4000], src.get("source_family"))
        return new_sources

    @staticmethod
    def _cross_lang_suspect(rk, case_lang: str,
                            lex_floor: float = 0.12) -> bool:
        """Result whose detected language differs from the case
        language and whose lexical tie to the case is near zero —
        needs an explicit LLM verdict before it can be accepted."""
        if rk.rejected or rk.relevance >= lex_floor:
            return False
        r = rk.result
        det = (r.detected_language or "").strip().lower()
        if not det:
            probe = " ".join(
                t for t in (r.title, r.snippet) if t)
            if len(probe) >= 30:
                det = detect_source_language(
                    None, r.title, r.snippet).language or ""
        return bool(det) and det != case_lang

    def _source_from_result(self, r, r_idx, lang, outcome, doc,
                            families) -> dict:
        """Build one honest source record — content only when retrieved."""
        title = (doc.title if doc and doc.title else r.title) or r.url
        publisher = (doc.publisher if doc and doc.publisher else None) \
            or domain_of(r.url)
        text = doc.text if doc else None
        lang_res = detect_source_language(
            text, title, r.snippet,
            html_lang=doc.language_meta if doc else None,
            query_language=lang)
        quality = classify_source(
            outcome.final_url or r.url, title,
            r.snippet, text)
        source: dict = {
            "_result_idx": r_idx,
            "_lang": lang,
            "url": outcome.final_url or r.url,
            "title": title[:1000],
            "publisher": publisher,
            "summary": (doc.meta_description if doc else None)
                       or (r.snippet or "")[:1000] or None,
            "source_type": TYPE_TO_SOURCE_TYPE.get(
                quality.source_type, "other"),
            "language": lang_res.language or lang,
            "declared_language": lang,
            "detected_language": lang_res.language,
            "language_confidence": lang_res.confidence,
            "language_detection_method": lang_res.method,
            "reliability": quality.quality_score,
            "published_at": (doc.published_at if doc else None)
                            or r.published_at,
            "retrieval_method": (
                "fetch_cache" if outcome.cache_hit
                else ("pypdf" if doc and doc.extractor == "pypdf"
                      else "http_fetch")),
            "retrieval_notes": (
                f"engine=searxng extractor={doc.extractor if doc else 'none'} "
                f"http={outcome.status_code} hops={outcome.redirect_hops} "
                f"cache={'hit' if outcome.cache_hit else 'miss'}"),
            "source_family": None,
            "content_status": doc.content_status if doc else "metadata_only",
        }
        if doc and doc.content_status == "full_text" and text:
            source["full_text"] = text
            source["full_text_available"] = True
        elif doc and doc.content_status == "partial_text" and text:
            source["excerpts"] = [{"text": text}]
        if families is not None and text:
            decision = families.classify(text)
            source["source_family"] = decision.family
            if decision.is_duplicate:
                source["_family_duplicate"] = True
            elif decision.family:
                # Same account, keep source but mark the family link.
                source["retrieval_notes"] = (
                    (source["retrieval_notes"] or "") +
                    f" family={decision.reason}")
        if not source["source_family"]:
            p = (publisher or "").strip().lower()
            t = re.sub(r"\s+", " ", title.lower()).strip()
            source["source_family"] = (
                f"{p}|{t[:80]}".strip("|") or None)
        return source

    # ------------------------------------------------------------------
    # Case discovery (topics) — real searches, LLM only structures
    # ------------------------------------------------------------------

    async def discover_cases(
        self, count: int, languages: list[str], theme: str | None,
        existing_titles: list[str], progress: Progress | None = None,
    ) -> dict:
        progress = progress or Progress()
        tel = _telemetry()
        self._bind_llm(tel)
        seeds = _discovery_queries(theme)
        found: list[dict] = []
        queries_out: list[dict] = []
        for lang in (languages or ["en"]):
            for q in seeds:
                progress.stage(f"searching:{lang}")
                queries_out.append({
                    "language": lang, "query": q, "purpose": "case_identity",
                    "priority": "1", "round": 0})
                try:
                    raw = await self.registry.search(q, lang, limit=10)
                except Exception as e:  # noqa: BLE001
                    log.warning("discovery search failed: %s", e)
                    raw = []
                tel["searches"] += 1
                for r in raw:
                    found.append({
                        "title": r.title, "snippet": r.snippet,
                        "url": r.url, "language": lang})
        progress.stage("analyzing")
        candidates = await self._extract_candidates(
            found, count, existing_titles, theme)
        return {
            "queries": queries_out,
            "candidates": candidates,
            "_telemetry": tel,
        }

    async def _extract_candidates(self, found, count, existing_titles,
                                  theme) -> list[dict]:
        if not self.gen or not found:
            return []
        system = """You identify real true-crime cases in raw search results.
A candidate is a SPECIFIC named case (a disappearance, murder, cold case,
missing-persons investigation) — not a channel, genre page or listicle topic.
Extract only cases explicitly supported by the provided results. JSON only."""
        user = __import__("json").dumps({
            "theme": theme,
            "existing_known_cases": existing_titles[:60],
            "results": found[:80],
            "max_candidates": count,
            "output_schema": {"candidates": [{
                "title": "canonical case name",
                "aliases": ["..."], "key_people": ["..."],
                "location": "...", "approximate_date": "...",
                "rationale": "why it fits a true-crime longform story",
                "narrative_potential": 0.0,
                "source_richness": 0.0,
                "languages_available": ["en"],
                "suggested_queries": ["..."],
                "angles": ["..."],
            }]},
        }, ensure_ascii=False)
        try:
            data, _ = await self.gen.generate_structured(
                "case_discovery_agent", system, user)
        except Exception as e:  # noqa: BLE001
            log.warning("candidate extraction failed: %s", e)
            return []
        return (data or {}).get("candidates") or []

    # ------------------------------------------------------------------
    # Video discovery via search engine (no LLM search)
    # ------------------------------------------------------------------

    async def discover_videos(self, case_context: dict,
                              languages: list[str],
                              youtube_backend=None,
                              progress: Progress | None = None) -> dict:
        progress = progress or Progress()
        tel = _telemetry()
        self._bind_llm(tel)
        videos: list[dict] = []
        title = case_context.get("title") or ""
        # Native query stems per language — a Persian documentary search
        # uses Persian terms, not a translated English string.
        stems = {
            "en": ["{t} documentary", "{t} true crime", "{t}"],
            "de": ["{t} Doku Fall", "{t} Verbrechen", "{t}"],
            "fa": ["{t} مستند پرونده", "{t} جنایت", "{t}"],
            "ar": ["{t} وثائقي قضية", "{t} جريمة", "{t}"],
        }
        queries_out: list[dict] = []
        backend = youtube_backend or self.registry
        for lang in (languages or ["en"]):
            qs = [s.format(t=title)
                  for s in stems.get(lang, stems["en"])]
            for q in qs:
                progress.stage(f"searching:{lang}")
                queries_out.append({
                    "language": lang, "query": q, "purpose": "youtube",
                    "priority": "2", "round": 0})
                try:
                    raw = await backend.search(q, lang, limit=10)
                except Exception as e:  # noqa: BLE001
                    log.warning("video search failed: %s", e)
                    raw = []
                tel["searches"] += 1
                from app.research_engine.urlnorm import youtube_video_id
                for r in raw:
                    vid = youtube_video_id(r.url) or (
                        (r.scores or {}).get("video_id"))
                    if not vid:
                        continue
                    videos.append({
                        "video_id": vid,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": r.title or vid,
                        "channel_name": (r.scores or {}).get("channel"),
                        "language": lang,
                        "published_at": r.published_at,
                        "description": (r.snippet or "")[:800],
                        "classification": "documentary",
                        "relevance": 0.6,
                        "discovery_query": q,
                        "research_language": lang,
                    })
        # Dedupe by video id, keep first provenance.
        seen = set()
        deduped = []
        for v in videos:
            if v["video_id"] in seen:
                continue
            seen.add(v["video_id"])
            deduped.append(v)
        return {
            "queries": queries_out,
            "videos": deduped[:30],
            "_telemetry": tel,
        }


def _telemetry() -> dict:
    return {
        "calls": 0, "searches": 0, "fetches": 0, "cache_hits": 0,
        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
        "search_cost_usd": 0.0, "fetch_cost_usd": 0.0,
        "aux_cost_usd": 0.0, "embedding_cost_usd": 0.0,
        "embedding_tokens": 0, "models_used": [],
        "engine": "truecrime_search_engine",
    }


def _acc_llm(tel: dict, usage) -> None:
    if not usage:
        return
    tel["calls"] += 1
    tel["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
    tel["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)
    cost = getattr(usage, "cost_usd", None)
    if cost:
        tel["aux_cost_usd"] += float(cost)
        tel["cost_usd"] += float(cost)
    model = getattr(usage, "model", None)
    if model and model not in tel["models_used"]:
        tel["models_used"].append(model)


def _ingest_canonical(r) -> str:
    """Canonical URL in the format research_jobs ingestion uses —
    scheme/www/query/fragment stripped."""
    canon = r.canonical_url or canonicalize_url(r.url) or r.url
    return canon.split("?")[0]


def _discovery_queries(theme: str | None) -> list[str]:
    base = [
        "unsolved disappearance documentary cold case",
        "missing persons cold case investigation",
        "unsolved murder cold case reopened",
        "true crime documentary full episode case",
        "baffling disappearance never solved",
    ]
    if theme:
        base.insert(0, f"{theme} true crime case")
    return base[:6]
