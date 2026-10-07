"""TrueCrimeSearchProvider — the ResearchProvider backed by our own
engine, not an LLM vendor.

Search/discovery runs on local infrastructure (SearXNG + fetcher +
extractor + index). APIMaster supplies only bounded intelligence
(query planning, reranking, gap analysis, summaries). Job lifecycle
is identical to the previous provider: start_* returns an external
id, poll() reports progress, ingestion happens in research_jobs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Awaitable, Callable

from app.core.ai_config import ai_config
from app.providers.base import ProviderError, ProviderJob, ResearchProvider
from app.providers.generation import get_generation_provider
from app.research_engine.embeddings import EmbeddingClient
from app.research_engine.fetch_cache import FetchCache
from app.research_engine.fetcher import DocumentFetcher
from app.research_engine.orchestrator import (
    EngineConfig,
    Progress,
    ResearchOrchestrator,
)
from app.research_engine.ratelimit import DomainRateLimiter
from app.research_engine.search import SearchBackendRegistry
from app.research_engine.search_adapters import (
    SearXNGSearchBackend,
    YouTubeSearchBackend,
)

log = logging.getLogger(__name__)

_JOBS: dict[str, dict[str, Any]] = {}


class _JobProgress:
    __slots__ = ("external_id",)

    def __init__(self, external_id: str | None):
        self.external_id = external_id

    def stage(self, stage: str):
        if self.external_id and self.external_id in _JOBS:
            _JOBS[self.external_id]["stage"] = stage

    def meta(self, **kw):
        if self.external_id and self.external_id in _JOBS:
            _JOBS[self.external_id]["meta"].update(kw)

    def live_telemetry(self, tel: dict):
        if self.external_id and self.external_id in _JOBS:
            _JOBS[self.external_id]["meta"]["telemetry"] = dict(tel)


def _engine_config() -> EngineConfig:
    cfg = ai_config.search_engine
    return EngineConfig(
        max_results_per_query=cfg.max_results_per_query,
        max_queries_per_language=cfg.max_queries_per_language,
        max_rounds_per_language=cfg.max_rounds_per_language,
        max_fetches_per_language=cfg.max_fetches_per_language,
        min_full_text_chars=cfg.min_full_text_chars,
        min_accept_score=cfg.min_accept_score,
        rerank_band=(cfg.rerank_band_low, cfg.rerank_band_high),
        max_rerank_candidates=cfg.max_rerank_candidates,
        enable_llm_rerank=cfg.enable_llm_rerank,
        enable_gap_analysis=cfg.enable_gap_analysis,
        consecutive_low_novelty_stop=cfg.consecutive_low_novelty_stop,
        max_duration_s=cfg.max_duration_s,
        fetch_quality_floor=cfg.fetch_quality_floor,
    )


class TrueCrimeSearchProvider(ResearchProvider):
    """First-party search/fetch/index engine with bounded LLM help."""

    name = "truecrime"

    def __init__(self):
        self.searxng_url = (
            os.getenv(ai_config.search_engine.searxng_url_env)
            or ai_config.search_engine.searxng_url
            or ""
        ).rstrip("/")
        self.searxng = SearXNGSearchBackend(
            base_url=self.searxng_url or None,
            timeout_s=ai_config.search_engine.request_timeout_s)
        self.youtube = YouTubeSearchBackend(searxng=self.searxng)
        self.registry = SearchBackendRegistry([self.searxng])
        cfg = ai_config.search_engine
        self.fetcher = DocumentFetcher(
            cache=FetchCache(max_age_s=cfg.fetch_cache_max_age_s),
            limiter=DomainRateLimiter(
                min_delay_s=cfg.min_delay_per_domain_s,
                max_concurrency=cfg.max_fetch_concurrency),
            timeout_s=cfg.fetch_timeout_s,
            max_bytes=cfg.fetch_max_bytes,
            resolve_dns=cfg.fetch_resolve_dns,
        )
        gen_cfg = ai_config.generation_provider()
        embed_model = gen_cfg.models.get("embedding")
        self.embedder = EmbeddingClient(
            base_url=gen_cfg.base_url,
            api_key=os.getenv(gen_cfg.secret_env),
            model=embed_model or "",
        )

    def _orchestrator(self) -> ResearchOrchestrator:
        try:
            gen = get_generation_provider()
            if not gen.is_configured():
                gen = None
        except Exception:  # noqa: BLE001
            gen = None
        return ResearchOrchestrator(
            registry=self.registry,
            fetcher=self.fetcher,
            generation_provider=gen,
            embedder=self.embedder if self.embedder.is_configured() else None,
            config=_engine_config(),
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.searxng_url)

    async def check_health(self) -> bool:
        h = await self.searxng.health()
        return bool(h.get("healthy"))

    async def provider_status(self) -> dict:
        """Settings-page health (Part 50): backend + fetcher + LLM +
        embedding — no OpenRouter fields."""
        searx = await self.searxng.health()
        emb_ok = self.embedder.is_configured()
        gen = None
        try:
            gen = get_generation_provider()
        except Exception:  # noqa: BLE001
            pass
        return {
            "provider": self.name,
            "engine": "truecrime_search_engine",
            "configured": self.is_configured(),
            "reachable": bool(searx.get("healthy")),
            "status": ("ok" if searx.get("healthy")
                       else ("missing_config" if not self.is_configured()
                             else "backend_unreachable")),
            "search_backend": searx,
            "fetcher": {"configured": True},
            "embedding": {
                "configured": emb_ok,
                "model": self.embedder.model or None,
                "cache_hits": self.embedder.cache_hits,
            },
            "llm": {
                "provider": ai_config.providers.generation,
                "configured": bool(gen and gen.is_configured()),
            },
        }

    # ------------------------------------------------------------------
    # Job interface
    # ------------------------------------------------------------------

    def _launch(
        self, runner: Callable[[_JobProgress], Awaitable[dict]]
    ) -> str:
        external_id = f"tce-{uuid.uuid4().hex[:16]}"
        _JOBS[external_id] = {
            "status": "running", "result": None, "error": None,
            "stage": "queued", "meta": {},
        }

        async def _run():
            try:
                result = await runner(_JobProgress(external_id))
                _JOBS[external_id].update(
                    status="completed", result=result, stage="completed")
            except Exception as e:  # noqa: BLE001
                log.exception("research job %s failed", external_id)
                kind = getattr(e, "kind", "engine_error")
                _JOBS[external_id].update(
                    status="failed",
                    error={"kind": kind, "message": str(e)[:500]},
                    stage="failed")

        asyncio.get_event_loop().create_task(_run())
        return external_id

    async def start_case_research(
        self,
        case_title: str,
        language: str,
        objective: str | None = None,
        research_languages: list[str] | None = None,
        context: dict | None = None,
    ) -> str:
        orch = self._orchestrator()
        case_context = dict(context or {})
        case_context.setdefault("title", case_title)
        case_context.setdefault("language", language)
        if objective:
            case_context["objective"] = objective
        corpus = case_context.pop("corpus", {})
        langs = research_languages or [language or "en"]

        async def run(p: _JobProgress):
            prog = Progress(stage_cb=p.stage, meta_cb=p.meta)
            result = await orch.research_case(
                case_context, langs, objective=objective,
                corpus=corpus, progress=prog)
            tel = result.get("_telemetry") or {}
            p.live_telemetry(tel)
            return result

        return self._launch(run)

    async def start_discovery(
        self,
        count: int,
        languages: list[str],
        theme: str,
        prefer_undercovered: bool,
        require_multiple_sources: bool,
        existing_titles: list[str],
        known_identities: list[dict] | None = None,
        include_unsolved: bool = False,
    ) -> str:
        orch = self._orchestrator()

        async def run(p: _JobProgress):
            prog = Progress(stage_cb=p.stage, meta_cb=p.meta)
            result = await orch.discover_cases(
                count=count, languages=languages or ["en"],
                theme=theme or None,
                existing_titles=existing_titles, progress=prog,
                known_identities=known_identities, include_unsolved=include_unsolved)
            p.live_telemetry(result.get("_telemetry") or {})
            return result

        return self._launch(run)

    async def start_video_discovery(
        self, case_title: str, languages: list[str]
    ) -> str:
        orch = self._orchestrator()

        async def run(p: _JobProgress):
            prog = Progress(stage_cb=p.stage, meta_cb=p.meta)
            result = await orch.discover_videos(
                {"title": case_title}, languages or ["en"],
                youtube_backend=self.youtube, progress=prog)
            p.live_telemetry(result.get("_telemetry") or {})
            return result

        return self._launch(run)

    async def poll(self, external_id: str) -> ProviderJob:
        job = _JOBS.get(external_id)
        if job is None:
            # Registry is in-memory — a job id we never saw cannot be
            # recovered after restart; fail clearly, never hang.
            return ProviderJob(
                external_id=external_id, status="failed",
                error="unknown research job (engine restarted?)",
                meta={"kind": "unknown_job"})
        err = job.get("error") or {}
        return ProviderJob(
            external_id=external_id,
            status=job["status"],
            result=job.get("result"),
            error=err.get("message"),
            meta={**(job.get("meta") or {}),
                  "stage": job.get("stage"),
                  "error_kind": err.get("kind")},
        )
