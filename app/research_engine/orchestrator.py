"""The research loop (Parts 6, 37-39, 41-43).

PLAN -> SEARCH -> RANK -> FETCH -> EXTRACT -> ANALYZE NOVELTY ->
IDENTIFY GAPS -> FOLLOW-UP -> STOP.

LLMs plan and analyze; they never see raw search-result dumps — only
selected retrieved content. Every acceptance/rejection is recorded so
the run replays deterministically in the query inspector.
"""
from __future__ import annotations

from app.agents.runner import run_agent
import logging
import re
import time
from dataclasses import dataclass

from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.providers.base import ProviderError
from app.services.budget import BudgetStopped
from app.services.tracking import track_research_call, stamp_run
from app.research_engine.dedupe import SourceFamilyDetector
from app.research_engine.content_filter import inspect_result
from app.research_engine.embeddings import cosine_matrix
from app.research_engine.extractor import extract
from app.research_engine.fetcher import DocumentFetcher
from app.research_engine.language import detect_source_language
from app.research_engine.planner import (
    GapAnalyzer,
    ResearchQueryPlanner,
    ResearchAssembler,
    ResultReranker,
    accumulate_telemetry,
)
from app.research_engine.ranking import (
    needs_llm_rerank,
    rank_results,
    select_for_fetch,
)
from app.research_engine.search import SearchBackendRegistry, SearchError
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
        search_diagnostics: list[dict] = []
        safety = {"blocked": {}, "dropped": {}, "accepted": 0}
        case_context["_safety"] = safety
        all_gaps: list[str] = list(case_context.get("research_gaps") or [])
        contradictions: list[str] = []
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
            # max_duration_s is the budget of each language, so a slow first
            # language cannot starve the others.
            stats = await self._research_language(
                lang, case_context, objective, seen_canonical,
                families, tel, queries_out, results_out, sources_out,
                all_gaps, contradictions, search_diagnostics, progress,
                time.monotonic())
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
            "search_diagnostics": search_diagnostics,
            "safety": safety,
            "_telemetry": tel,
        }

    async def _research_language(
        self, lang: str, case_context: dict, objective: str | None,
        seen_canonical: set[str], families: SourceFamilyDetector,
        tel: dict, queries_out: list, results_out: list,
        sources_out: list, all_gaps: list, contradictions: list,
        search_diagnostics: list,
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
            search_failed = False
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
                except SearchError as exc:
                    detail = dict(exc.details or {})
                    detail.setdefault("query", pq.query)
                    detail.setdefault("provider", exc.backend)
                    detail.setdefault("result_count", 0)
                    detail.setdefault("retries", 0)
                    detail.setdefault("error_kind", exc.kind)
                    search_diagnostics.append(detail)
                    queries_out[q_idx]["search"] = detail
                    tel["search_errors"] = tel.get("search_errors", 0) + 1
                    search_failed = True
                    stats["stop_reason"] = "search_unavailable"
                    log.warning("research search failed query=%r status=%s results=%s retries=%s",
                                pq.query, detail.get("http_status"),
                                detail.get("result_count", 0), detail.get("retries", 0))
                    break
                backend_detail = getattr(self.registry.backends[0],
                                         "last_search_diagnostics", None) or {}
                detail = dict(backend_detail)
                detail.setdefault("query", pq.query)
                search_diagnostics.append(detail)
                queries_out[q_idx]["search"] = detail
                stats["search_calls"] += 1
                tel["searches"] += 1
                search_cost = float(detail.get("estimated_cost_usd") or 0.0)
                tel["search_cost_usd"] += search_cost
                tel["cost_usd"] += search_cost
                stats["cost_usd"] += search_cost
                stats["sources_found"] += len(raw)
                new_this_round += await self._process_results(
                    raw, q_idx, lang, case_context, seen_canonical,
                    families, tel, results_out, sources_out, stats,
                    fetched_budget=lambda: self.cfg.max_fetches_per_language - stats["fetch_calls"],
                    progress=progress, safety=case_context["_safety"])
                if stats["fetch_calls"] >= self.cfg.max_fetches_per_language:
                    break

            if search_failed:
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
        fetched_budget, progress, safety,
    ) -> int:
        """Rank -> accept/reject -> fetch top candidates -> extract."""
        similarity = None
        safety.setdefault("blocked", {})
        safety.setdefault("dropped", {})
        safety.setdefault("accepted", 0)
        safe_raw = []
        for candidate in raw:
            reason, relevance = inspect_result(candidate, case_context, candidate.query, lang)
            if reason:
                bucket = safety["blocked"] if reason.startswith("blocked_") else safety["dropped"]
                bucket[reason] = bucket.get(reason, 0) + 1
                results_out.append({
                    "query_idx": q_idx,
                    "url": candidate.url,
                    "canonical_url": candidate.canonical_url or candidate.url,
                    "title": candidate.title,
                    "snippet": candidate.snippet,
                    "rank": candidate.rank,
                    "result_language": candidate.detected_language or lang,
                    "relevance": relevance,
                    "accepted": False,
                    "rejection_reason": reason,
                    "fetch_status": "not_fetched",
                })
                stats["rejected"] += 1
                continue
            safe_raw.append(candidate)
        raw = safe_raw
        safety["accepted"] += len(raw)
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
                f"engine=brave extractor={doc.extractor if doc else 'none'} "
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
        known_identities: list[dict] | None = None, include_unsolved: bool = False,
    ) -> dict:
        """Case discovery -> duplicate checker -> status verifier ->
        ranking (RECENT + SOLVED + NEVER USED; app/lifecycle/selection)."""
        from app.lifecycle.identity import IdentityIndex
        from app.lifecycle.selection import prepare_candidates

        progress = progress or Progress()
        tel = _telemetry()
        self._bind_llm(tel)
        if self.gen is None:
            raise ProviderError(
                "generation_unconfigured",
                "Discovery needs the configured generation provider for candidate extraction; "
                "set TRUECRIME_APIMASTER_API_KEY and retry.",
            )
        time_range = ai_config.case_selection.discovery_time_range or None
        found: list[dict] = []
        queries_out: list[dict] = []
        search_diagnostics: list[dict] = []
        for lang in (languages or ["en"]):
            for q in _discovery_queries(theme, lang):
                progress.stage(f"searching:{lang}")
                queries_out.append({
                    "language": lang, "query": q, "purpose": "case_identity",
                    "priority": "1", "round": 0, "time_range": time_range})
                raw = await self.registry.search(q, lang, limit=10, time_range=time_range)
                tel["searches"] += 1
                diagnostic = {
                    "query": q,
                    "language": lang,
                    "result_count": len(raw),
                    "engines": sorted({
                        engine
                        for item in raw
                        for engine in (item.scores or {}).get("engines", [])
                        if engine
                    }),
                    "sample_results": [
                        {"title": item.title, "url": item.url}
                        for item in raw[:3]
                    ],
                }
                for backend in getattr(self.registry, "backends", []):
                    detail = getattr(backend, "last_search_diagnostics", None)
                    if isinstance(detail, dict) and detail.get("query") == q:
                        diagnostic.update({
                            "http_status": detail.get("http_status"),
                            "provider": detail.get("provider") or "brave",
                        })
                        break
                search_diagnostics.append(diagnostic)
                for r in raw:
                    found.append({
                        "title": r.title, "snippet": r.snippet,
                        "url": r.url, "language": lang,
                        "published_at": r.published_at})
        progress.stage("analyzing")
        candidates = await self._extract_candidates(
            found, min(max(count * 3, count + 4), 30), existing_titles, theme, tel)
        progress.stage("duplicate_check")
        prepared = await prepare_candidates(
            candidates, found, IdentityIndex.from_list(known_identities),
            count=count, include_unsolved=include_unsolved,
            registry=self.registry, gen=self.gen, progress=progress)
        tel["searches"] += prepared["stats"].get("status_searches", 0)
        return {
            "queries": queries_out,
            "search_diagnostics": search_diagnostics,
            "candidates": prepared["candidates"],
            "selection_stats": prepared["stats"],
            "_telemetry": tel,
        }

    async def _extract_candidates(self, found, count, existing_titles,
                                  theme, tel: dict | None = None) -> list[dict]:
        if not self.gen or not found:
            return []
        system = prompt("research_engine/orchestrator/extract_candidates")
        user = __import__("json").dumps({
            "theme": theme,
            "existing_known_cases": existing_titles[:60],
            "results": found[:90],
            "max_candidates": count,
            "output_schema": {"candidates": [{
                "title": "canonical case name",
                "aliases": ["..."], "key_people": ["victim / suspect full names"],
                "location": "town, region, country", "approximate_date": "...",
                "incident_date": "YYYY-MM-DD | YYYY-MM | YYYY",
                "latest_development_date": "date of the newest reported development",
                "resolution_status": "SOLVED|UNSOLVED|STATUS_UNDER_REVIEW|UNKNOWN",
                "resolution_evidence": "what the results report (e.g. 'convicted May 2026')",
                "source_urls": ["..."], "identifiers": ["police / court case numbers"],
                "rationale": "why it fits a true-crime longform story",
                "narrative_potential": 0.0,
                "source_richness": 0.0,
                "languages_available": ["en"],
                "suggested_queries": ["..."],
                "angles": ["..."],
            }]},
        }, ensure_ascii=False)
        try:
            with track_research_call("Candidate Extractor") as run:
                data, res = await run_agent("research_engine.discover_cases", self.gen, user, system=system)
                stamp_run(run, res, "case_discovery_agent")
            accumulate_telemetry(tel, res)
        except BudgetStopped:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("candidate extraction failed: %s", e)
            return []
        return (data or {}).get("candidates") or []

    # ------------------------------------------------------------------
    # Video discovery via search engine (no LLM search)
    # ------------------------------------------------------------------

    # Part 3(e) items 1/2: cheap, deterministic channel-type classification
    # and relevance scoring (no new LLM call, same cost-conscious pattern
    # as app.services.transcript_quality) — this used to be hardcoded to
    # "documentary"/0.6 for every single candidate, for every case.
    _CHANNEL_TYPE_KEYWORDS = {
        "news": ("news", "breaking", "nachrichten", "bbc", "cnn", "abc news",
                "cbs news", "nbc news", "sky news", "al jazeera", "reuters"),
        "official": ("police department", "sheriff", "fbi", "court tv",
                    "courtroom", "trial footage", "prosecutor",
                    "department of justice", "press conference", "bodycam"),
        "interview": ("interview", "sits down with", "exclusive interview",
                     "in conversation with", "q&a with"),
        "documentary": ("documentary", "full documentary",
                       "true crime documentary", "investigation discovery",
                       "crime files"),
        "archive": ("archive footage", "archival", "newsreel", "vintage footage",
                   "from the archive"),
    }

    @classmethod
    def _classify_video(cls, title: str | None, channel_name: str | None,
                        description: str | None) -> str:
        text = f"{title or ''} {channel_name or ''} {description or ''}".lower()
        for ctype, keywords in cls._CHANNEL_TYPE_KEYWORDS.items():
            if any(k in text for k in keywords):
                return ctype
        return "unknown"

    _TOKEN = re.compile(r"[a-zA-Z؀-ۿÀ-ɏ]{3,}")

    @classmethod
    def _video_relevance(cls, video_text: str, case_title: str) -> float:
        case_tokens = {t.lower() for t in cls._TOKEN.findall(case_title or "")}
        if not case_tokens:
            return 0.5  # no case-title signal at all — neutral, not a guess
        video_tokens = {t.lower() for t in cls._TOKEN.findall(video_text or "")}
        if not video_tokens:
            return 0.0
        return round(min(1.0, len(case_tokens & video_tokens) / len(case_tokens)), 4)

    async def discover_videos(self, case_context: dict,
                              languages: list[str],
                              progress: Progress | None = None) -> dict:
        progress = progress or Progress()
        tel = _telemetry()
        self._bind_llm(tel)
        videos: list[dict] = []
        title = case_context.get("title") or ""
        # Native query stems per language — a Persian documentary search
        # uses Persian terms, not a translated English string.
        # restricted to youtube.com: a plain web search for a case title
        # rarely returns watch pages.
        queries_out: list[dict] = []
        backend = self.registry
        for lang in (languages or ["en"]):
            qs = ["site:youtube.com " + s.format(t=title)
                  for s in ai_config.multilingual.video_stems(lang)]
            for q in qs:
                progress.stage(f"searching:{lang}")
                queries_out.append({
                    "language": lang, "query": q, "purpose": "youtube",
                    "priority": "2", "round": 0})
                raw = await backend.search(q, lang, limit=10)
                tel["searches"] += 1
                from app.research_engine.urlnorm import youtube_video_id
                for r in raw:
                    vid = youtube_video_id(r.url) or (
                        (r.scores or {}).get("video_id"))
                    if not vid:
                        continue
                    video_title = r.title or vid
                    channel_name = (r.scores or {}).get("channel")
                    description = (r.snippet or "")[:800]
                    videos.append({
                        "video_id": vid,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": video_title,
                        "channel_name": channel_name,
                        "language": lang,
                        "published_at": r.published_at,
                        "description": description,
                        "classification": self._classify_video(
                            video_title, channel_name, description),
                        "relevance": self._video_relevance(
                            f"{video_title} {description}", title),
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


def _ingest_canonical(r) -> str:
    """Canonical URL in the format research_jobs ingestion uses —
    scheme/www/query/fragment stripped."""
    canon = r.canonical_url or canonicalize_url(r.url) or r.url
    return canon.split("?")[0]


def _discovery_queries(theme: str | None, language: str = "en") -> list[str]:
    """Recent, resolved cases first (config case_selection.seed_queries)."""
    from datetime import datetime, timezone

    year = datetime.now(timezone.utc).year
    normalized_theme = " ".join((theme or "").strip().lower().split())
    if normalized_theme in {
        "historical cases (before 2000)",
        "historical cases before 2000",
    }:
        historical = {
            "en": [
                "historical murder case before 2000",
                "cold case solved before 2000",
                "murder trial verdict before 2000",
                "missing person case before 2000",
                "historical true crime case before 2000",
            ],
            "de": [
                "historischer Mordfall vor 2000",
                "Cold Case vor 2000 aufgeklärt",
                "Mordprozess Urteil vor 2000",
            ],
            "fa": ["پرونده قتل تاریخی قبل از سال ۲۰۰۰", "پرونده حل‌شده قبل از ۲۰۰۰"],
            "ar": ["قضية قتل تاريخية قبل عام 2000", "قضية باردة حُلت قبل عام 2000"],
        }
        return (historical.get(language) or historical["en"])[:6]
    seeds = (ai_config.case_selection.seed_queries.get(language)
             or ai_config.case_selection.seed_queries.get("en") or [])
    base = [q.replace("{year}", str(year)).replace("{last_year}", str(year - 1))
            for q in seeds]
    if theme and theme.strip().lower() not in ("true crime", ""):
        base.insert(0, f"{theme} case verdict {year}")
    return base[:6]
