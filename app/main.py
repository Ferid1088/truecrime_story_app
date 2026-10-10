import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session

from app.core.tasks import spawn
from app.db.base import Base, engine, get_db
from app.db.models import (
    Case,
    Source,
    SourceChunk,
    Fact,
    Contradiction,
    StoryVersion,
    EditorialBlueprint,
    DiscoveryCandidate,
    AgentRun,
    ResearchJob,
    ResearchQuery,
    ResearchResult,
    ResearchSourceLink,
    VideoSource,
    TranscriptSegment,
    TranscriptClaim,
    ClaimCluster,
    NarrativeInsight,
)
from app.schemas import (
    TopicDiscoveryRequest,
    CreateCaseRequest,
    UpdateCaseRequest,
    InvestigateCandidateRequest,
    AddSourceRequest,
    GenerateStoryRequest,
    ImproveStoryRequest,
    VoiceRenderRequest,
    DynamicEQPreviewRequest,
    check_target_minutes,
)
from app.core.config import settings
from app.core.ai_config import ai_config
from app.utils import slugify, utc_now
from app.agents.story import (
    MASTER_ROLES,
    StoryPipeline,
    current_evidence_fingerprint,
    estimate_narrative_capacity,
    research_gap_plan,
    stored_evidence_fingerprint,
    structure_without_text,
)
from app.agents.localization import LocalizationPipeline
from app.documentary.audio import AudioToolError
from app.documentary.voice_blocks import plan_for_version
from app.documentary.voice_render import VoiceRenderer
from app.documentary.blueprint import (
    NarrativeDirector,
    blueprint_dict,
    latest_blueprint,
)
from app.documentary.performance import performance_for_version
from app.documentary.production.audio import render_documentary_audio
from app.documentary.spoken import SpokenNarrator
from app.documentary.audio_director import (
    AudioDirector,
    audio_plan_dict,
    latest_audio_plan,
)
from app.providers import get_research_provider
from app.providers.voice import VoiceProviderError
from app.providers.base import ProviderError
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services import research_jobs
from app.services.readiness import build_readiness


Base.metadata.create_all(bind=engine)


def _add_model_columns(conn, model) -> None:
    """ALTER TABLE ADD COLUMN for every column the model declares and the
    existing table lacks (SQLite; scalar defaults only)."""
    table = model.__tablename__
    if table not in inspect(conn).get_table_names():
        return
    have = {c["name"] for c in inspect(conn).get_columns(table)}
    for col in model.__table__.columns:
        if col.name in have:
            continue
        ddl = f'ALTER TABLE {table} ADD COLUMN "{col.name}" {col.type.compile(dialect=conn.dialect)}'
        default = getattr(col.default, "arg", None)
        if isinstance(default, bool):
            ddl += f" DEFAULT {int(default)}"
        elif isinstance(default, (int, float)):
            ddl += f" DEFAULT {default}"
        elif isinstance(default, str):
            ddl += " DEFAULT '" + default.replace("'", "''") + "'"
        conn.execute(text(ddl))


def _ensure_columns():
    """Lightweight guard for columns added after the initial schema.

    create_all only creates missing tables, never alters existing ones,
    so add missing columns to pre-existing SQLite tables explicitly.
    """
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("facts")}
        if "event_date" not in cols:
            conn.execute(text("ALTER TABLE facts ADD COLUMN event_date VARCHAR(40)"))
        for col, ddl in {
            "original_claim": "ALTER TABLE facts ADD COLUMN original_claim TEXT",
            "original_language": "ALTER TABLE facts ADD COLUMN original_language VARCHAR(20) DEFAULT 'en'",
            "narrative_value": "ALTER TABLE facts ADD COLUMN narrative_value VARCHAR(40)",
            "evidence_strength": "ALTER TABLE facts ADD COLUMN evidence_strength VARCHAR(40)",
            "supporting_text": "ALTER TABLE facts ADD COLUMN supporting_text TEXT",
            "chunk_ids_json": "ALTER TABLE facts ADD COLUMN chunk_ids_json TEXT DEFAULT '[]'",
            "quote_status": "ALTER TABLE facts ADD COLUMN quote_status VARCHAR(40)",
            "speaker": "ALTER TABLE facts ADD COLUMN speaker VARCHAR(300)",
            "people_json": "ALTER TABLE facts ADD COLUMN people_json TEXT DEFAULT '[]'",
            "locations_json": "ALTER TABLE facts ADD COLUMN locations_json TEXT DEFAULT '[]'",
            "transcript_claim_ids_json": "ALTER TABLE facts ADD COLUMN transcript_claim_ids_json TEXT DEFAULT '[]'",
        }.items():
            if col not in cols:
                conn.execute(text(ddl))
        story_cols = {c["name"] for c in inspect(conn).get_columns("story_versions")}
        if "language" not in story_cols:
            conn.execute(text("ALTER TABLE story_versions ADD COLUMN language VARCHAR(20) DEFAULT 'fa'"))
        for col, ddl in {
            "generation_provider": "ALTER TABLE story_versions ADD COLUMN generation_provider VARCHAR(50)",
            "generation_model": "ALTER TABLE story_versions ADD COLUMN generation_model VARCHAR(200)",
            "similarity_status": "ALTER TABLE story_versions ADD COLUMN similarity_status VARCHAR(30) DEFAULT 'not_evaluated'",
            "status": "ALTER TABLE story_versions ADD COLUMN status VARCHAR(20) DEFAULT 'draft'",
            "is_best": "ALTER TABLE story_versions ADD COLUMN is_best BOOLEAN DEFAULT 0",
            "text_hash": "ALTER TABLE story_versions ADD COLUMN text_hash VARCHAR(64)",
            "narrative_structure": "ALTER TABLE story_versions ADD COLUMN narrative_structure TEXT",
            "kind": "ALTER TABLE story_versions ADD COLUMN kind VARCHAR(20) DEFAULT 'direct'",
            "master_version_id": "ALTER TABLE story_versions ADD COLUMN master_version_id INTEGER",
            "derived_from_master_version": "ALTER TABLE story_versions ADD COLUMN derived_from_master_version INTEGER",
            "native_quality_score": "ALTER TABLE story_versions ADD COLUMN native_quality_score FLOAT",
            "semantic_consistency_score": "ALTER TABLE story_versions ADD COLUMN semantic_consistency_score FLOAT",
            "factual_consistency_score": "ALTER TABLE story_versions ADD COLUMN factual_consistency_score FLOAT",
        }.items():
            if col not in story_cols:
                conn.execute(text(ddl))
        # similarity_score must be nullable (not_evaluated -> NULL), but the
        # original schema created it NOT NULL. SQLite cannot relax a column
        # constraint in place, so rebuild the table once if needed.
        nullable = {
            c["name"]: c["nullable"] for c in inspect(conn).get_columns("story_versions")
        }
        if nullable.get("similarity_score") is False:
            cols = ", ".join(
                f'"{c["name"]}"' for c in inspect(conn).get_columns("story_versions")
            )
            conn.execute(text("ALTER TABLE story_versions RENAME TO story_versions_old"))
            conn.execute(text("""
                CREATE TABLE story_versions (
                    id INTEGER NOT NULL PRIMARY KEY,
                    case_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    narrative_angle TEXT NOT NULL,
                    story_text TEXT NOT NULL,
                    engagement_score FLOAT NOT NULL,
                    similarity_score FLOAT,
                    critic_notes TEXT,
                    created_at DATETIME NOT NULL,
                    language VARCHAR(20) DEFAULT 'fa',
                    generation_provider VARCHAR(50),
                    generation_model VARCHAR(200),
                    similarity_status VARCHAR(30) DEFAULT 'not_evaluated',
                    status VARCHAR(20) DEFAULT 'draft',
                    is_best BOOLEAN DEFAULT 0,
                    text_hash VARCHAR(64),
                    narrative_structure TEXT,
                    kind VARCHAR(20) DEFAULT 'direct',
                    master_version_id INTEGER,
                    derived_from_master_version INTEGER,
                    native_quality_score FLOAT,
                    semantic_consistency_score FLOAT,
                    factual_consistency_score FLOAT,
                    FOREIGN KEY(case_id) REFERENCES cases (id)
                )
            """))
            conn.execute(text(
                f"INSERT INTO story_versions ({cols}) SELECT {cols} FROM story_versions_old"
            ))
            # Scores recorded by the old no-op similarity critic (0 source
            # texts) were placeholders, not real evaluations.
            conn.execute(text(
                "UPDATE story_versions SET similarity_score = NULL "
                "WHERE similarity_status = 'not_evaluated'"
            ))
            conn.execute(text("DROP TABLE story_versions_old"))
        run_cols = {c["name"] for c in inspect(conn).get_columns("agent_runs")}
        for col, ddl in {
            "provider": "ALTER TABLE agent_runs ADD COLUMN provider VARCHAR(50)",
            "model": "ALTER TABLE agent_runs ADD COLUMN model VARCHAR(200)",
            "role": "ALTER TABLE agent_runs ADD COLUMN role VARCHAR(50)",
            "temperature": "ALTER TABLE agent_runs ADD COLUMN temperature FLOAT",
            "fallback_used": "ALTER TABLE agent_runs ADD COLUMN fallback_used BOOLEAN DEFAULT 0",
            "input_tokens": "ALTER TABLE agent_runs ADD COLUMN input_tokens INTEGER",
            "output_tokens": "ALTER TABLE agent_runs ADD COLUMN output_tokens INTEGER",
            "total_tokens": "ALTER TABLE agent_runs ADD COLUMN total_tokens INTEGER",
            "estimated_cost_usd": "ALTER TABLE agent_runs ADD COLUMN estimated_cost_usd FLOAT",
            "generation_id": "ALTER TABLE agent_runs ADD COLUMN generation_id VARCHAR(200)",
            "text_hash": "ALTER TABLE agent_runs ADD COLUMN text_hash VARCHAR(64)",
        }.items():
            if col not in run_cols:
                conn.execute(text(ddl))
        src_cols = {c["name"] for c in inspect(conn).get_columns("sources")}
        for col, ddl in {
            "summary": "ALTER TABLE sources ADD COLUMN summary TEXT",
            "summary_en": "ALTER TABLE sources ADD COLUMN summary_en TEXT",
            "content_status": "ALTER TABLE sources ADD COLUMN content_status VARCHAR(30) DEFAULT 'summary_only'",
            "value_flags": "ALTER TABLE sources ADD COLUMN value_flags TEXT",
            "published_at": "ALTER TABLE sources ADD COLUMN published_at VARCHAR(80)",
            "retrieved_at": "ALTER TABLE sources ADD COLUMN retrieved_at DATETIME",
            "research_provider": "ALTER TABLE sources ADD COLUMN research_provider VARCHAR(50)",
            "external_reference": "ALTER TABLE sources ADD COLUMN external_reference VARCHAR(500)",
            "status": "ALTER TABLE sources ADD COLUMN status VARCHAR(50) DEFAULT 'active'",
            "source_family": "ALTER TABLE sources ADD COLUMN source_family VARCHAR(300)",
            "retrieval_method": "ALTER TABLE sources ADD COLUMN retrieval_method VARCHAR(50)",
            "retrieval_notes": "ALTER TABLE sources ADD COLUMN retrieval_notes TEXT",
            "declared_language": "ALTER TABLE sources ADD COLUMN declared_language VARCHAR(20)",
            "detected_language": "ALTER TABLE sources ADD COLUMN detected_language VARCHAR(20)",
            "language_confidence": "ALTER TABLE sources ADD COLUMN language_confidence FLOAT",
            "language_detection_method": "ALTER TABLE sources ADD COLUMN language_detection_method VARCHAR(40)",
        }.items():
            if col not in src_cols:
                conn.execute(text(ddl))
        if "research_jobs" in inspect(conn).get_table_names():
            rj_cols = {c["name"] for c in inspect(conn).get_columns("research_jobs")}
            for col, ddl in {
                "current_stage": "ALTER TABLE research_jobs ADD COLUMN current_stage VARCHAR(60)",
                "model": "ALTER TABLE research_jobs ADD COLUMN model VARCHAR(200)",
                "profile": "ALTER TABLE research_jobs ADD COLUMN profile VARCHAR(60)",
                "error_code": "ALTER TABLE research_jobs ADD COLUMN error_code VARCHAR(50)",
                "search_calls": "ALTER TABLE research_jobs ADD COLUMN search_calls INTEGER",
                "fetch_calls": "ALTER TABLE research_jobs ADD COLUMN fetch_calls INTEGER",
                "input_tokens": "ALTER TABLE research_jobs ADD COLUMN input_tokens INTEGER",
                "output_tokens": "ALTER TABLE research_jobs ADD COLUMN output_tokens INTEGER",
                "total_tokens": "ALTER TABLE research_jobs ADD COLUMN total_tokens INTEGER",
                "model_cost_usd": "ALTER TABLE research_jobs ADD COLUMN model_cost_usd FLOAT",
                "search_cost_usd": "ALTER TABLE research_jobs ADD COLUMN search_cost_usd FLOAT",
                "fetch_cost_usd": "ALTER TABLE research_jobs ADD COLUMN fetch_cost_usd FLOAT",
                "total_cost_usd": "ALTER TABLE research_jobs ADD COLUMN total_cost_usd FLOAT",
                "sources_discovered": "ALTER TABLE research_jobs ADD COLUMN sources_discovered INTEGER",
                "sources_accepted": "ALTER TABLE research_jobs ADD COLUMN sources_accepted INTEGER",
                "sources_rejected": "ALTER TABLE research_jobs ADD COLUMN sources_rejected INTEGER",
                "languages_completed": "ALTER TABLE research_jobs ADD COLUMN languages_completed TEXT",
            }.items():
                if col not in rj_cols:
                    conn.execute(text(ddl))
        if "documentary_jobs" in inspect(conn).get_table_names():
            dj = {c["name"]: c for c in inspect(conn).get_columns("documentary_jobs")}
            for col, ddl in {
                "refresh_visuals": "ALTER TABLE documentary_jobs ADD COLUMN refresh_visuals BOOLEAN DEFAULT 0",
                "from_zero": "ALTER TABLE documentary_jobs ADD COLUMN from_zero BOOLEAN DEFAULT 0",
                "target_minutes": "ALTER TABLE documentary_jobs ADD COLUMN target_minutes FLOAT",
                "batch_id": "ALTER TABLE documentary_jobs ADD COLUMN batch_id VARCHAR(40)",
                "production_type": "ALTER TABLE documentary_jobs ADD COLUMN production_type VARCHAR(20) DEFAULT 'original'",
                "follow_up_id": "ALTER TABLE documentary_jobs ADD COLUMN follow_up_id INTEGER",
            }.items():
                if col not in dj:
                    conn.execute(text(ddl))
            if dj.get("master_version_id", {}).get("nullable") is False:
                # "from zero" jobs have no master yet: relax NOT NULL by
                # rebuilding the table once (SQLite cannot alter it).
                cols = ", ".join(f'"{c}"' for c in
                                 [c["name"] for c in inspect(conn).get_columns("documentary_jobs")])
                conn.execute(text("ALTER TABLE documentary_jobs RENAME TO documentary_jobs_old"))
                for (idx,) in conn.execute(text(
                        "SELECT name FROM sqlite_master WHERE type='index' AND "
                        "tbl_name='documentary_jobs_old' AND name NOT LIKE 'sqlite_%'")).all():
                    conn.execute(text(f'DROP INDEX "{idx}"'))
                conn.commit()
                from app.db.models import DocumentaryJob
                DocumentaryJob.__table__.create(conn)
                conn.execute(text(
                    f"INSERT INTO documentary_jobs ({cols}) SELECT {cols} FROM documentary_jobs_old"))
                conn.execute(text("DROP TABLE documentary_jobs_old"))
        # Case lifecycle + media library columns: added from the models
        # (new nullable / defaulted columns only).
        from app.db.models import (DiscoveryCandidate as _DC, EpisodeIdentity as _EI,
                                   HostScene as _HS, ProductionScript as _PS,
                                   VisualAsset as _VA, Video as _VD)
        for model in (Case, _DC, _VA, _HS, _PS, _VD, _EI):
            _add_model_columns(conn, model)
        conn.commit()


_ensure_columns()


def _backfill_case_uids():
    from app.db.base import SessionLocal
    from app.identity.titles import backfill_case_uids

    with SessionLocal() as _db:
        backfill_case_uids(_db)


_backfill_case_uids()


@asynccontextmanager
async def _lifespan(_app):
    """At startup, work left "running" by the previous process is marked
    interrupted (resumable, nothing deleted). The unsolved-case monitor
    runs about twice a week in this process (TRUECRIME_DISABLE_SCHEDULER=1
    keeps it off)."""
    from app.documentary.recovery import recover_after_restart
    from app.lifecycle import scheduler

    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        recover_after_restart(db)
    except Exception as e:  # never block the app from starting
        logging.getLogger(__name__).error("startup recovery failed: %s", e)
    finally:
        db.close()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(
    lifespan=_lifespan,
    title="TrueCrime Story Studio",
    version="1.0.0",
    description="Research → Facts → Contradictions → Story Direction → Writing → Critique",
)

from app.documentary.api import router as documentary_router  # noqa: E402
from app.lifecycle.api import router as lifecycle_router, resolution_dict  # noqa: E402
from app.naming.api import router as naming_router  # noqa: E402
from app.thumbnails.api import router as thumbnail_router  # noqa: E402
from app.documentary.studio_api import router as studio_router  # noqa: E402

app.include_router(documentary_router)
app.include_router(lifecycle_router)
app.include_router(naming_router)
app.include_router(thumbnail_router)
app.include_router(studio_router)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def source_dict(s: Source) -> dict:
    return {
        "id": s.id,
        "case_id": s.case_id,
        "title": s.title,
        "url": s.url,
        "source_type": s.source_type,
        "language": s.language,
        "declared_language": s.declared_language,
        "detected_language": s.detected_language,
        "language_confidence": s.language_confidence,
        "language_detection_method": s.language_detection_method,
        "publisher": s.publisher,
        "raw_text": s.raw_text,
        "notes": s.notes,
        "reliability_score": s.reliability_score,
        "is_authorized_text": s.is_authorized_text,
        "summary": s.summary,
        "summary_en": s.summary_en,
        "content_status": s.content_status,
        "value_flags": json.loads(s.value_flags or "[]"),
        "source_family": s.source_family,
        "retrieval_method": s.retrieval_method,
        "retrieval_notes": s.retrieval_notes,
        "chunk_count": len(s.chunks or []),
        "raw_text_length": len(s.raw_text) if s.raw_text else 0,
        "published_at": s.published_at,
        "retrieved_at": s.retrieved_at,
        "research_provider": s.research_provider,
        "external_reference": s.external_reference,
        "status": s.status,
        "created_at": s.created_at,
    }


def fact_dict(f: Fact) -> dict:
    return {
        "id": f.id,
        "case_id": f.case_id,
        "claim": f.claim,
        "original_claim": f.original_claim,
        "original_language": f.original_language,
        "narrative_value": f.narrative_value,
        "evidence_strength": f.evidence_strength,
        "quote_status": f.quote_status,
        "speaker": f.speaker,
        "people": json.loads(f.people_json or "[]"),
        "locations": json.loads(f.locations_json or "[]"),
        "supporting_text": f.supporting_text,
        "category": f.category,
        "confidence": f.confidence,
        "disputed": f.disputed,
        "event_date": f.event_date,
        "source_ids": json.loads(f.source_ids_json or "[]"),
        "chunk_ids": json.loads(f.chunk_ids_json or "[]"),
    }


def contradiction_dict(c: Contradiction) -> dict:
    return {
        "id": c.id,
        "case_id": c.case_id,
        "topic": c.topic,
        "description": c.description,
        "severity": c.severity,
        "source_ids": json.loads(c.source_ids_json or "[]"),
    }


def story_meta_dict(s: StoryVersion) -> dict:
    critic = {}
    if s.critic_notes:
        try:
            critic = json.loads(s.critic_notes)
        except (ValueError, TypeError):
            critic = {}
    return {
        "id": s.id,
        "case_id": s.case_id,
        "version": s.version,
        "narrative_angle": s.narrative_angle,
        "language": s.language,
        "kind": s.kind,
        "master_version_id": s.master_version_id,
        "derived_from_master_version": s.derived_from_master_version,
        "native_quality_score": s.native_quality_score,
        "semantic_consistency_score": s.semantic_consistency_score,
        "factual_consistency_score": s.factual_consistency_score,
        "engagement_score": s.engagement_score,
        "similarity_score": s.similarity_score,
        "similarity_status": s.similarity_status,
        "status": s.status,
        "is_best": s.is_best,
        "word_count": len(s.story_text.split()),
        "dimensions": critic.get("dimensions") or {},
        "problems": critic.get("problems") or [],
        "rewrite_instructions": critic.get("rewrite_instructions") or [],
        "generation_provider": s.generation_provider,
        "generation_model": s.generation_model,
        "text_hash": s.text_hash,
        # Section text duplicates story_text; the voice-blocks endpoint
        # exposes per-act text where it is actually needed.
        "narrative_structure": structure_without_text(
            json.loads(s.narrative_structure)
        )
        if s.narrative_structure
        else None,
        "created_at": s.created_at,
    }


def story_full_dict(s: StoryVersion) -> dict:
    return {**story_meta_dict(s), "story_text": s.story_text}


def agent_run_dict(r: AgentRun) -> dict:
    return {
        "id": r.id,
        "case_id": r.case_id,
        "agent_name": r.agent_name,
        "status": r.status,
        "started_at": r.started_at,
        "completed_at": r.completed_at,
        "duration_ms": r.duration_ms,
        "input_summary": r.input_summary,
        "output_summary": r.output_summary,
        "error": r.error,
        "provider": r.provider,
        "model": r.model,
        "role": r.role,
        "temperature": r.temperature,
        "fallback_used": r.fallback_used,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "total_tokens": r.total_tokens,
        "estimated_cost_usd": r.estimated_cost_usd,
        "generation_id": r.generation_id,
        "text_hash": r.text_hash,
    }


def _case_stats(db: Session, case_id: int) -> dict:
    latest_story = (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id)
        .order_by(StoryVersion.is_best.desc(), StoryVersion.version.desc())
        .first()
    )
    return {
        "sources": db.query(Source).filter(Source.case_id == case_id).count(),
        "facts": db.query(Fact).filter(Fact.case_id == case_id).count(),
        "contradictions": db.query(Contradiction)
        .filter(Contradiction.case_id == case_id)
        .count(),
        "story_versions": db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id)
        .count(),
        "latest_story_version": latest_story.version if latest_story else None,
        "engagement_score": latest_story.engagement_score if latest_story else None,
        "generation_usage": _generation_usage(db, case_id),
    }


def _generation_usage(db: Session, case_id: int) -> dict:
    """Aggregate reported provider usage across a case's agent runs,
    split by provider (Part 12 — research vs generation cost).

    None when providers never reported usage — never estimated."""
    runs = db.query(AgentRun).filter(
        AgentRun.case_id == case_id, AgentRun.total_tokens.isnot(None)
    ).all()
    if not runs:
        return {
            "input_tokens": None, "output_tokens": None,
            "total_tokens": None, "estimated_cost_usd": None,
            "cost_by_provider": {},
        }
    costs = [r.estimated_cost_usd for r in runs if r.estimated_cost_usd is not None]
    by_provider: dict[str, dict] = {}
    for r in runs:
        bucket = by_provider.setdefault(
            r.provider or "unknown",
            {"calls": 0, "total_tokens": 0, "estimated_cost_usd": 0.0},
        )
        bucket["calls"] += 1
        bucket["total_tokens"] += r.total_tokens or 0
        bucket["estimated_cost_usd"] += r.estimated_cost_usd or 0.0
    return {
        "input_tokens": sum(r.input_tokens or 0 for r in runs),
        "output_tokens": sum(r.output_tokens or 0 for r in runs),
        "total_tokens": sum(r.total_tokens or 0 for r in runs),
        "estimated_cost_usd": sum(costs) if costs else None,
        "cost_by_provider": by_provider,
    }


def _get_case_or_404(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


# ---------------------------------------------------------------------------
# Health / settings
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/settings/status")
def settings_status(db: Session = Depends(get_db)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    gen_provider = get_generation_provider()
    gen_cfg = ai_config.generation_provider()
    res_cfg = ai_config.research_provider()
    return {
        "generation": {
            "provider": ai_config.providers.generation,
            "base_url": gen_cfg.base_url,
            "configured": gen_provider.is_configured(),
            "models": gen_cfg.models,
            "routing": gen_cfg.routing,
            "fallbacks": gen_cfg.fallbacks,
        },
        "research": {
            "provider": ai_config.providers.research,
            "models": res_cfg.models,
            "routing": res_cfg.routing,
        },
        "search": {
            "provider": "searxng",
            "configured": get_research_provider().is_configured(),
        },
        "youtube": {"configured": bool(settings.youtube_api_key)},
        "research_provider": {
            "provider": get_research_provider().name,
            "configured": get_research_provider().is_configured(),
        },
        "multilingual": {
            "research_languages": ai_config.multilingual.research_languages,
            "canonical_language": ai_config.multilingual.canonical_language,
            "localization_languages": ai_config.multilingual.localization_languages,
        },
        "database": {"connected": db_ok, "url": settings.database_url.split("///")[0]},
    }


@app.get("/api/integrations/research/status")
async def research_status():
    """TrueCrime Search Engine status — SearXNG backend, fetcher,
    embedding and LLM-intelligence health. No OpenRouter."""
    provider = get_research_provider()
    if hasattr(provider, "provider_status"):
        return await provider.provider_status()
    configured = provider.is_configured()
    return {
        "provider": provider.name,
        "configured": configured,
        "reachable": await provider.check_health() if configured else False,
    }


@app.get("/api/integrations/search-engine/status")
async def search_engine_status():
    """Alias for the research status — explicit engine-scoped name."""
    return await research_status()


@app.get("/api/integrations/apimaster/status")
async def apimaster_status():
    """APIMaster = generation only (Part 8): classified status plus
    per-alias model availability from GET /models."""
    status = await _generation_provider_status()
    gen_cfg = ai_config.generation_provider()
    return {
        **status,
        "models": gen_cfg.models,
        "routing": gen_cfg.routing,
        "fallbacks": gen_cfg.fallbacks,
    }


async def _generation_provider_status() -> dict:
    """Classified generation-provider status — one probe, reused by the
    status endpoint, the readiness report and the generation preflight."""
    provider = get_generation_provider()
    if hasattr(provider, "provider_status"):
        return await provider.provider_status()
    configured = provider.is_configured()
    return {
        "provider": provider.name,
        "configured": configured,
        "reachable": (
            await provider.check_health() if configured else False
        ),
        "authorized": True,
        "status": "unknown",
    }


async def _require_generation_authorized() -> dict:
    """Part 23: no expensive generation while the provider cannot authorize.

    Checks configured → reachable → authorized; fails with a classified
    503 rather than letting an expensive pipeline die mid-run."""
    st = await _generation_provider_status()
    if not st.get("configured"):
        raise HTTPException(
            status_code=503,
            detail={"code": "provider_not_configured",
                    "provider_status": st,
                    "message": "Generation provider is not configured."},
        )
    if not st.get("reachable"):
        raise HTTPException(
            status_code=503,
            detail={"code": "provider_unreachable",
                    "provider_status": st,
                    "message": "Generation provider is unreachable."},
        )
    if st.get("authorized") is False:
        raise HTTPException(
            status_code=503,
            detail={"code": "provider_unauthorized",
                    "provider_status": st,
                    "message": (
                        "Generation provider has a permission/quota problem "
                        f"({st.get('status')}). Fix the provider before "
                        "running expensive generation."
                    )},
        )
    return st


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    cases = db.query(Case).order_by(Case.created_at.desc()).all()

    def count_status(*statuses: str) -> int:
        return sum(1 for c in cases if c.status in statuses)

    recent = []
    for c in cases[:10]:
        stats = _case_stats(db, c.id)
        recent.append(
            {
                "id": c.id,
                "title": c.canonical_title,
                "status": c.status,
                **resolution_dict(c),
                "created_at": c.created_at,
                **stats,
            }
        )

    agent_rows = (
        db.query(AgentRun)
        .order_by(AgentRun.started_at.desc())
        .limit(30)
        .all()
    )
    agent_activity = {}
    for r in agent_rows:
        if r.agent_name not in agent_activity:
            agent_activity[r.agent_name] = {
                "agent_name": r.agent_name,
                "status": r.status,
                "started_at": r.started_at,
                "duration_ms": r.duration_ms,
            }

    return {
        "stats": {
            "total_cases": len(cases),
            "cases_researched": count_status("researched", "writing", "story_ready", "producing", "rendered",
                                             "published", "completed"),
            "stories_completed": db.query(StoryVersion).count(),
            "cases_waiting": count_status("new"),
            "sources_collected": db.query(Source).count(),
            "facts_extracted": db.query(Fact).count(),
            "contradictions_found": db.query(Contradiction).count(),
        },
        "recent_cases": recent,
        "agent_activity": list(agent_activity.values()),
        # previously covered UNSOLVED cases that are now SOLVED: waiting
        # for the user's decision about an update video
        "follow_up_candidates": _pending_follow_ups(db),
        "resolution_counts": {
            s: sum(1 for c in cases if (c.resolution_status or "UNKNOWN") == s)
            for s in ("SOLVED", "UNSOLVED", "UNKNOWN", "STATUS_UNDER_REVIEW")},
    }


def _pending_follow_ups(db: Session) -> list[dict]:
    from app.db.models import FollowUpCandidate
    from app.lifecycle.followups import candidate_dict

    rows = (db.query(FollowUpCandidate).filter(FollowUpCandidate.state == "pending")
            .order_by(FollowUpCandidate.id.desc()).all())
    return [candidate_dict(db, fu) for fu in rows]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@app.post("/api/topics/discover")
async def discover_topics(payload: TopicDiscoveryRequest, db: Session = Depends(get_db)):
    """Candidate discovery runs as a job on the search engine (duplicate and
    status checks included); there is no second, unchecked path."""
    if not get_research_provider().is_configured():
        raise HTTPException(status_code=503,
                            detail="Search engine not configured (set TRUECRIME_SEARXNG_URL).")
    try:
        job = await research_jobs.start_discovery_job(db, payload)
    except ProviderError as e:
        raise HTTPException(status_code=503, detail=f"Research provider unavailable: {e}")
    return {"job_id": job.id, "status": job.status}


@app.get("/api/research-jobs/{job_id}")
async def get_research_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(ResearchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research job not found")
    job = await research_jobs.poll_job(db, job)
    return research_jobs.job_dict(job)


@app.get("/api/research-jobs")
async def list_research_jobs(case_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(ResearchJob).order_by(ResearchJob.created_at.desc())
    if case_id is not None:
        query = query.filter(ResearchJob.case_id == case_id)
    rows = query.limit(50).all()
    out = []
    for job in rows:
        job = await research_jobs.poll_job(db, job, ingest=False)   # list views only read
        out.append(research_jobs.job_dict(job, include_result=False))
    return out


@app.get("/api/research-jobs/{job_id}/queries")
def research_job_queries(job_id: int, db: Session = Depends(get_db)):
    """Query inspector (Part 24): every executed query with its results —
    language, purpose, round, acceptance and rejection reasons."""
    job = db.get(ResearchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research job not found")
    queries = (
        db.query(ResearchQuery)
        .filter(ResearchQuery.research_job_id == job_id)
        .order_by(ResearchQuery.id)
        .all()
    )
    return [
        {
            "id": q.id,
            "language": q.language,
            "query": q.query_text,
            "purpose": q.purpose,
            "priority": q.priority,
            "round": q.round,
            "created_at": q.created_at,
            "results": [
                {
                    "id": r.id,
                    "url": r.url,
                    "final_url": r.final_url,
                    "title": r.title,
                    "rank": r.rank,
                    "model": r.model,
                    "result_language": r.result_language,
                    "relevance_score": r.relevance_score,
                    "credibility_score": r.credibility_score,
                    "novelty_score": r.novelty_score,
                    "fetch_status": r.fetch_status,
                    "accepted": r.accepted,
                    "rejection_reason": r.rejection_reason,
                    "source_ids": [l.source_id for l in r.source_links],
                }
                for r in q.results
            ],
        }
        for q in queries
    ]


@app.get("/api/cases/{case_id}/sources/{source_id}")
def get_source_detail(
    case_id: int, source_id: int, db: Session = Depends(get_db)
):
    """Source detail with full discovery provenance (Part 25): every
    query — across languages and jobs — that surfaced this source."""
    s = (
        db.query(Source)
        .filter(Source.id == source_id, Source.case_id == case_id)
        .first()
    )
    if not s:
        raise HTTPException(status_code=404, detail="Source not found")
    links = (
        db.query(ResearchSourceLink, ResearchResult, ResearchQuery)
        .join(
            ResearchResult,
            ResearchResult.id == ResearchSourceLink.research_result_id,
        )
        .join(
            ResearchQuery,
            ResearchQuery.id == ResearchResult.research_query_id,
        )
        .filter(ResearchSourceLink.source_id == source_id)
        .all()
    )
    seen_queries: set[int] = set()
    discovered_by = []
    for link, res, q in links:
        if q.id in seen_queries:
            continue
        seen_queries.add(q.id)
        discovered_by.append(
            {
                "query": q.query_text,
                "language": q.language,
                "purpose": q.purpose,
                "round": q.round,
                "link_type": link.link_type,
            }
        )
    d = source_dict(s)
    d["discovered_by"] = discovered_by
    return d


@app.get("/api/cases/{case_id}/corpus-search")
async def corpus_search(
    case_id: int,
    q: str,
    language: str | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """Hybrid corpus search (Part 49): BM25 + dense-embedding cosine fused
    over this case's downloaded research chunks. Multilingual — a Persian
    query matches English evidence semantically via the embedding model."""
    case = _get_case_or_404(db, case_id)
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="q is required")
    limit = max(1, min(int(limit or 10), 50))

    from app.providers import get_research_provider
    from app.research_engine.index import (
        CorpusIndex,
        IndexedChunk,
        INDEX_CACHE,
    )

    idx = INDEX_CACHE.get(case_id)
    if idx is None:
        provider = get_research_provider()
        embedder = getattr(provider, "embedder", None)
        if embedder is not None and not embedder.is_configured():
            embedder = None
        idx = CorpusIndex(embedder=embedder)
        rows = (
            db.query(SourceChunk, Source)
            .join(Source, Source.id == SourceChunk.source_id)
            .filter(Source.case_id == case_id)
            .all()
        )
        await idx.build(
            [
                IndexedChunk(
                    chunk_id=c.id,
                    source_id=s.id,
                    source_title=s.title or "",
                    language=c.language or s.language,
                    text=c.text or "",
                    location=c.page_or_location or c.section_title,
                )
                for c, s in rows
            ],
            embed=True,
        )
        INDEX_CACHE.put(case_id, idx)

    hits = await idx.search(q, limit=limit, language=language)
    src_urls = {
        s.id: s.url
        for s in db.query(Source)
        .filter(Source.id.in_({h.source_id for h in hits}))
        .all()
    } if hits else {}
    return {
        "query": q,
        "language": language,
        "case_id": case_id,
        "chunks_indexed": len(idx.chunks),
        "dense_enabled": idx._matrix is not None,
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "source_id": h.source_id,
                "source_title": h.source_title,
                "source_url": src_urls.get(h.source_id),
                "language": h.language,
                "text": h.text,
                "location": h.location,
                "score": h.score,
                "bm25_score": h.bm25_score,
                "dense_score": h.dense_score,
            }
            for h in hits
        ],
    }


@app.get("/api/discovery/history")
def discovery_history(state: str | None = None, db: Session = Depends(get_db)):
    """Every suggestion with its status, ranking reason — and the
    duplicates/filtered ones with the reason they were not suggested."""
    from app.lifecycle.selection import candidate_dict

    q = db.query(DiscoveryCandidate)
    if state:
        q = q.filter(DiscoveryCandidate.state == state)
    rows = q.order_by(DiscoveryCandidate.created_at.desc()).limit(200).all()
    return [candidate_dict(r) for r in rows]


def _duplicate_or_409(db: Session, identity, force: bool, exclude_candidate: int | None = None):
    """The duplicate checker runs before any case is created (identity:
    people, places, dates, URLs, aliases — not just the title)."""
    from app.lifecycle.identity import IdentityIndex

    index = IdentityIndex([i for i in IdentityIndex.from_db(db).items
                           if i.kind == "case"])
    verdict = index.check(identity)
    if verdict.duplicate and not force:
        raise HTTPException(status_code=409, detail={
            "message": "This case already exists in the system.", **verdict.to_dict()})
    return verdict


def _unique_slug(db: Session, title: str) -> str:
    base_slug = slugify(title)
    slug, i = base_slug, 2
    while db.query(Case).filter(Case.slug == slug).first():
        slug = f"{base_slug}-{i}"
        i += 1
    return slug


@app.post("/api/discovery/{candidate_id}/investigate")
def investigate_candidate(
    candidate_id: int,
    payload: InvestigateCandidateRequest,
    db: Session = Depends(get_db),
):
    from app.lifecycle.identity import candidate_identity
    from app.lifecycle.status import set_resolution

    cand = db.get(DiscoveryCandidate, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if cand.case_id and db.get(Case, cand.case_id):
        existing = db.get(Case, cand.case_id)
        return {"id": existing.id, "canonical_title": existing.canonical_title,
                "slug": existing.slug, "existing": True}
    _duplicate_or_409(db, candidate_identity(cand), payload.force)

    case = Case(
        canonical_title=cand.title,
        slug=_unique_slug(db, cand.title),
        language=payload.language,
        summary=cand.rationale,
        aliases_json=cand.aliases_json or "[]",
        people_json=cand.people_json or "[]",
        location=cand.location,
        incident_date=cand.incident_date,
        latest_development_date=cand.latest_development_date,
        origin="discovery",
    )
    db.add(case)
    db.flush()
    set_resolution(
        db, case, cand.resolution_status or "UNKNOWN", changed_by="discovery",
        reason=cand.resolution_evidence or cand.suggestion_reason or "from the suggestion",
        confidence=cand.resolution_confidence,
        sources=[{"url": u} for u in json.loads(cand.source_urls_json or "[]")][:10],
        summary=cand.resolution_evidence, commit=False)
    cand.selected = True
    cand.rejected = False
    cand.state = "accepted"
    cand.case_id = case.id
    db.commit()
    db.refresh(case)
    return {"id": case.id, "canonical_title": case.canonical_title, "slug": case.slug,
            **resolution_dict(case)}


@app.post("/api/discovery/{candidate_id}/ignore")
def ignore_candidate(candidate_id: int, db: Session = Depends(get_db)):
    cand = db.get(DiscoveryCandidate, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cand.rejected = True
    cand.selected = False
    cand.state = "ignored"   # stays in the history: never suggested again
    db.commit()
    return {"id": cand.id, "rejected": True}


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@app.post("/api/cases")
def create_case(payload: CreateCaseRequest, db: Session = Depends(get_db)):
    from app.lifecycle.identity import Identity
    from app.lifecycle.status import set_resolution

    identity = Identity.build("new", None, payload.canonical_title, aliases=payload.aliases,
                              people=payload.people, location=payload.location,
                              dates=[payload.incident_date])
    _duplicate_or_409(db, identity, payload.force)
    row = Case(
        canonical_title=payload.canonical_title,
        slug=_unique_slug(db, payload.canonical_title),
        language=payload.language,
        summary=payload.summary,
        aliases_json=json.dumps(payload.aliases, ensure_ascii=False),
        people_json=json.dumps(payload.people, ensure_ascii=False),
        location=payload.location,
        incident_date=payload.incident_date,
        origin="manual",
    )
    db.add(row)
    db.flush()
    set_resolution(db, row, payload.resolution_status, changed_by="user",
                   reason="set when the case was created", commit=False)
    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "canonical_title": row.canonical_title,
        "slug": row.slug,
        **resolution_dict(row),
    }


@app.get("/api/cases")
def list_cases(status: str | None = None, q: str | None = None,
               resolution: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Case)
    if status:
        query = query.filter(Case.status == status)
    if resolution and resolution.upper() != "ALL":
        query = query.filter(Case.resolution_status == resolution.upper())
    if q:
        query = query.filter(Case.canonical_title.ilike(f"%{q}%"))
    rows = query.order_by(Case.created_at.desc()).all()
    out = []
    for r in rows:
        last_run = (
            db.query(AgentRun)
            .filter(AgentRun.case_id == r.id)
            .order_by(AgentRun.started_at.desc())
            .first()
        )
        last_story = (
            db.query(StoryVersion)
            .filter(StoryVersion.case_id == r.id)
            .order_by(StoryVersion.created_at.desc())
            .first()
        )
        timestamps = [r.created_at]
        if last_run and last_run.started_at:
            timestamps.append(last_run.started_at)
        if last_story and last_story.created_at:
            timestamps.append(last_story.created_at)
        out.append(
            {
                "id": r.id,
                "title": r.canonical_title,
                "status": r.status,
                **resolution_dict(r),
                "language": r.language,
                "created_at": r.created_at,
                "last_activity": max(timestamps),
                **_case_stats(db, r.id),
            }
        )
    return out


@app.get("/api/cases/{case_id}")
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    sources = db.query(Source).filter(Source.case_id == case_id).all()
    latest_story = (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id)
        .order_by(StoryVersion.is_best.desc(), StoryVersion.version.desc())
        .first()
    )
    return {
        "id": case.id,
        "title": case.canonical_title,
        "slug": case.slug,
        "status": case.status,
        **resolution_dict(case),
        "aliases": json.loads(case.aliases_json or "[]"),
        "people": json.loads(case.people_json or "[]"),
        "location": case.location,
        "incident_date": case.incident_date,
        "latest_development_date": case.latest_development_date,
        "origin": case.origin,
        "language": case.language,
        "summary": case.summary,
        "created_at": case.created_at,
        "languages_found": sorted({s.language for s in sources if s.language}),
        "narrative_angle": latest_story.narrative_angle if latest_story else None,
        **_case_stats(db, case_id),
    }


@app.patch("/api/cases/{case_id}")
def update_case(case_id: int, payload: UpdateCaseRequest, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    if payload.status is not None:
        case.status = payload.status
    if payload.canonical_title is not None:
        case.canonical_title = payload.canonical_title
    if payload.summary is not None:
        case.summary = payload.summary
    if payload.aliases is not None:
        case.aliases_json = json.dumps(payload.aliases, ensure_ascii=False)
    if payload.people is not None:
        case.people_json = json.dumps(payload.people, ensure_ascii=False)
    if payload.location is not None:
        case.location = payload.location or None
    if payload.incident_date is not None:
        case.incident_date = payload.incident_date or None
    db.commit()
    return {"id": case.id, "status": case.status, **resolution_dict(case)}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@app.post("/api/cases/{case_id}/sources")
def add_source(case_id: int, payload: AddSourceRequest, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)

    row = Source(
        case_id=case_id,
        title=payload.title,
        url=payload.url,
        source_type=payload.source_type,
        language=payload.language,
        publisher=payload.publisher,
        raw_text=payload.raw_text,
        notes=payload.notes,
        reliability_score=payload.reliability_score,
        is_authorized_text=payload.is_authorized_text,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id}


@app.get("/api/cases/{case_id}/sources")
def list_sources(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    rows = (
        db.query(Source)
        .filter(Source.case_id == case_id)
        .order_by(Source.created_at.desc())
        .all()
    )
    return [source_dict(s) for s in rows]


@app.delete("/api/cases/{case_id}/sources/{source_id}")
def delete_source(case_id: int, source_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    source = (
        db.query(Source)
        .filter(Source.id == source_id, Source.case_id == case_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    db.delete(source)
    db.commit()
    return {"deleted": source_id}


# ---------------------------------------------------------------------------
# Research data
# ---------------------------------------------------------------------------


@app.post("/api/cases/{case_id}/research")
async def run_research(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    if not get_research_provider().is_configured():
        raise HTTPException(status_code=503,
                            detail="Search engine not configured (set TRUECRIME_SEARXNG_URL).")
    try:
        job = await research_jobs.start_research_job(db, case)
    except ProviderError as e:
        raise HTTPException(status_code=503, detail=f"Research provider unavailable: {e}")
    return {"job_id": job.id, "status": job.status}


@app.get("/api/cases/{case_id}/research")
def get_research(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)

    facts = db.query(Fact).filter(Fact.case_id == case_id).all()
    contradictions = db.query(Contradiction).filter(Contradiction.case_id == case_id).all()
    sources = db.query(Source).filter(Source.case_id == case_id).all()

    return {
        "sources": [
            {
                "id": s.id,
                "title": s.title,
                "url": s.url,
                "source_type": s.source_type,
                "reliability_score": s.reliability_score,
            }
            for s in sources
        ],
        "facts": [
            {
                "id": f.id,
                "claim": f.claim,
                "category": f.category,
                "confidence": f.confidence,
                "disputed": f.disputed,
                "event_date": f.event_date,
                "source_ids": json.loads(f.source_ids_json or "[]"),
            }
            for f in facts
        ],
        "contradictions": [
            {
                "id": c.id,
                "topic": c.topic,
                "description": c.description,
                "severity": c.severity,
                "source_ids": json.loads(c.source_ids_json or "[]"),
            }
            for c in contradictions
        ],
    }


@app.get("/api/cases/{case_id}/facts")
def list_facts(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    rows = db.query(Fact).filter(Fact.case_id == case_id).all()
    return [fact_dict(f) for f in rows]


@app.get("/api/cases/{case_id}/timeline")
def case_timeline(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    rows = (
        db.query(Fact)
        .filter(Fact.case_id == case_id, Fact.event_date.isnot(None))
        .all()
    )
    items = [fact_dict(f) for f in rows]
    items.sort(key=lambda f: f["event_date"])
    return items


@app.get("/api/cases/{case_id}/contradictions")
def list_contradictions(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    rows = db.query(Contradiction).filter(Contradiction.case_id == case_id).all()
    return [contradiction_dict(c) for c in rows]


# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------


@app.post("/api/cases/{case_id}/generate-story")
async def generate_story(case_id: int, payload: GenerateStoryRequest, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    await _require_generation_authorized()

    try:
        story = await StoryPipeline().run(
            db=db,
            case=case,
            target_minutes=payload.target_minutes,
            language=payload.language,
            tone=payload.tone,
            iterations=payload.iterations,
        )
        return {
            "story_version_id": story.id,
            "version": story.version,
            "engagement_score": story.engagement_score,
            "similarity_score": story.similarity_score,
            "similarity_status": story.similarity_status,
            "status": story.status,
            "story_text": story.story_text,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cases/{case_id}/improve-story")
async def improve_story(case_id: int, payload: ImproveStoryRequest, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.id == payload.story_version_id, StoryVersion.case_id == case_id)
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story version not found")

    try:
        new_version = await StoryPipeline().improve(
            db=db, case=case, story_version=story, instruction=payload.instruction
        )
        return story_full_dict(new_version)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cases/{case_id}/stories")
def list_stories(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    rows = (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id)
        .order_by(StoryVersion.version.desc())
        .all()
    )
    return [story_meta_dict(s) for s in rows]


@app.get("/api/cases/{case_id}/stories/{version_id}")
def get_story_version(case_id: int, version_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.id == version_id, StoryVersion.case_id == case_id)
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story version not found")
    return story_full_dict(story)


@app.get("/api/cases/{case_id}/stories/{version_id}/voice-blocks")
def story_voice_blocks(case_id: int, version_id: int, db: Session = Depends(get_db)):
    """Sentence-safe TTS block plan for one story version (read-only, no
    provider cost). `evidence_current` is False when research changed
    after this version was written (its evidence IDs are then stale)."""
    _get_case_or_404(db, case_id)
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.id == version_id, StoryVersion.case_id == case_id)
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story version not found")
    plan = plan_for_version(story)
    stored_fp = stored_evidence_fingerprint(story)
    plan["evidence_current"] = (
        None if stored_fp is None
        else stored_fp == current_evidence_fingerprint(db, case_id)
    )
    return plan


def _story_or_404(db: Session, case_id: int, version_id: int) -> StoryVersion:
    _get_case_or_404(db, case_id)
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.id == version_id, StoryVersion.case_id == case_id)
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story version not found")
    return story


def _voice_summary(manifest: dict) -> dict:
    """Manifest without the (long) word-level timeline."""
    timeline = manifest.get("timeline") or {}
    return {
        **{k: v for k, v in manifest.items() if k != "timeline"},
        "timeline_blocks": timeline.get("blocks") or [],
        "timeline_word_count": len(timeline.get("words") or []),
    }


@app.post("/api/cases/{case_id}/stories/{version_id}/voice/render")
async def render_story_voice(
    case_id: int, version_id: int, payload: VoiceRenderRequest,
    db: Session = Depends(get_db),
):
    """Render narration for a story version: TTS per voice block with
    word timestamps, independent speech-to-text check (with automatic
    re-take of failing blocks), loudness normalization, one narration
    track + timeline. Unchanged blocks come from cache (no cost).
    `max_seconds` renders only the opening (e.g. a 3-minute pilot).
    With a usable editorial blueprint, blocks follow its performance
    script (style per beat, dramatic pauses, silences)."""
    story = _story_or_404(db, case_id, version_id)
    plan = performance_for_version(db, story)
    try:
        manifest = await render_documentary_audio(
            plan, case_id=case_id, story_version_id=version_id,
            max_seconds=payload.max_seconds, style=payload.style,
            force_block_ids=payload.force_block_ids,
            with_music=payload.with_music,
        )
    except VoiceProviderError as e:
        code = 503 if e.kind == "missing_credentials" else 502
        raise HTTPException(status_code=code, detail={"code": e.kind, "message": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except AudioToolError as e:
        raise HTTPException(status_code=500, detail=str(e))
    summary = _voice_summary(manifest)
    summary["blueprint_used"] = plan.get("blueprint_used", False)
    summary["directed"] = plan.get("directed", False)
    return summary


@app.post("/api/cases/{case_id}/stories/{version_id}/spoken")
async def create_spoken_version(
    case_id: int, version_id: int, language: str, db: Session = Depends(get_db),
):
    """Spoken storytelling version of a story (needs its blueprint): the
    text the way a person tells a true story, natively in `language`
    (en/de/fa/ar), beat by beat, facts unchanged — checked for meaning by
    one model and for storyteller tone by another."""
    story = _story_or_404(db, case_id, version_id)
    await _require_generation_authorized()
    case = _get_case_or_404(db, case_id)
    try:
        spoken = await SpokenNarrator().create(db, case, story, language)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GenerationError as e:
        raise HTTPException(status_code=502, detail={"code": e.kind, "message": str(e)})
    body = story_full_dict(spoken)
    body["spoken_checks"] = (json.loads(spoken.critic_notes or "{}").get("spoken"))
    return body


def _blueprint_row_for(db: Session, story: StoryVersion):
    """The blueprint behind a story: its own, or — for a spoken version —
    the one its beats were written from (shared by all languages)."""
    if story.kind == "spoken":
        try:
            bp_id = json.loads(story.narrative_structure or "{}").get("blueprint_id")
        except (ValueError, TypeError):
            bp_id = None
        return db.get(EditorialBlueprint, bp_id) if bp_id else None
    return latest_blueprint(db, story.id)


@app.post("/api/cases/{case_id}/stories/{version_id}/audio-plan")
async def create_audio_plan(case_id: int, version_id: int, db: Session = Depends(get_db)):
    """Audio director's plan (breaths, music beds, bridges, emotional
    moments, stings, silences) for this story's blueprint — shared by
    every language version made from it."""
    story = _story_or_404(db, case_id, version_id)
    row = _blueprint_row_for(db, story)
    if not row or row.status == "invalid":
        raise HTTPException(status_code=409, detail="Create a valid blueprint first.")
    await _require_generation_authorized()
    case = _get_case_or_404(db, case_id)
    try:
        plan = await AudioDirector().create(db, case, row)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GenerationError as e:
        raise HTTPException(status_code=502, detail={"code": e.kind, "message": str(e)})
    return audio_plan_dict(plan)


@app.get("/api/cases/{case_id}/stories/{version_id}/audio-plan")
def get_audio_plan(case_id: int, version_id: int, db: Session = Depends(get_db)):
    story = _story_or_404(db, case_id, version_id)
    row = _blueprint_row_for(db, story)
    plan = latest_audio_plan(db, row.id) if row else None
    if not plan:
        raise HTTPException(status_code=404, detail="No audio plan for this story")
    return audio_plan_dict(plan)


@app.post("/api/cases/{case_id}/stories/{version_id}/blueprint")
async def create_story_blueprint(
    case_id: int, version_id: int, db: Session = Depends(get_db),
):
    """Editorial blueprint for a story: beats (paragraph ranges per act)
    with purpose, reveals, listener questions, attention/visual/audio
    intents and pauses — validated deterministically (status valid |
    needs_review | invalid)."""
    story = _story_or_404(db, case_id, version_id)
    await _require_generation_authorized()
    case = _get_case_or_404(db, case_id)
    try:
        row = await NarrativeDirector().create(db, case, story)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except GenerationError as e:
        raise HTTPException(status_code=502, detail={"code": e.kind, "message": str(e)})
    return blueprint_dict(row)


@app.get("/api/cases/{case_id}/stories/{version_id}/blueprint")
def get_story_blueprint(case_id: int, version_id: int, db: Session = Depends(get_db)):
    _story_or_404(db, case_id, version_id)
    row = latest_blueprint(db, version_id)
    if not row:
        raise HTTPException(status_code=404, detail="No blueprint for this story version")
    return blueprint_dict(row)


@app.get("/api/cases/{case_id}/stories/{version_id}/performance")
def get_story_performance(case_id: int, version_id: int, db: Session = Depends(get_db)):
    """Performance script (read-only, no provider cost): voice blocks with
    style, beat ranges and the pause after each block."""
    story = _story_or_404(db, case_id, version_id)
    return performance_for_version(db, story)


def _voice_dir(story: StoryVersion):
    return VoiceRenderer.out_dir_for(story.case_id, story.language, story.id)


@app.get("/api/cases/{case_id}/stories/{version_id}/voice")
def story_voice_manifest(case_id: int, version_id: int, db: Session = Depends(get_db)):
    story = _story_or_404(db, case_id, version_id)
    path = _voice_dir(story) / "manifest.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No narration rendered yet")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/cases/{case_id}/stories/{version_id}/voice/narration.mp3")
def story_voice_audio(case_id: int, version_id: int, db: Session = Depends(get_db)):
    story = _story_or_404(db, case_id, version_id)
    path = _voice_dir(story) / "narration.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No narration rendered yet")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/cases/{case_id}/stories/{version_id}/voice/narration_original.mp3")
def story_voice_audio_original(case_id: int, version_id: int,
                               db: Session = Depends(get_db)):
    """The narration BEFORE dynamic EQ — the A side of the studio's
    original/enhanced comparison."""
    story = _story_or_404(db, case_id, version_id)
    path = _voice_dir(story) / "narration_original.mp3"
    if not path.exists():
        raise HTTPException(status_code=404,
                            detail="No pre-EQ original (enhancement was not applied)")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/cases/{case_id}/stories/{version_id}/voice/dynamics")
def story_voice_dynamics(case_id: int, version_id: int,
                         db: Session = Depends(get_db)):
    """Gain-reduction report: per-band thresholds, when/where/how much
    reduction was applied (events) and the GR curve over time."""
    story = _story_or_404(db, case_id, version_id)
    path = _voice_dir(story) / "dynamics.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No dynamic-EQ report yet")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/cases/{case_id}/stories/{version_id}/voice/eq-preview")
def story_voice_eq_preview(case_id: int, version_id: int,
                           payload: DynamicEQPreviewRequest,
                           db: Session = Depends(get_db)):
    """Pre-rendered preview: process the opening of the ORIGINAL
    narration with the given overrides and return a small mp3 plus the
    full gain-reduction report. Originals are never touched."""
    from app.documentary import dynamic_eq as DEQ

    story = _story_or_404(db, case_id, version_id)
    out = _voice_dir(story)
    src = out / "narration.wav"
    if not src.exists():
        raise HTTPException(status_code=404, detail="No narration rendered yet")
    language = story.language or "en"
    cfg = ai_config.dynamic_eq
    update: dict = {}
    if payload.enabled is not None:
        update["enabled"] = payload.enabled
    if payload.strength is not None:
        update["strength"] = payload.strength
    if payload.max_atten_db is not None:
        update["bands"] = [
            b.model_copy(update={"max_atten_db": payload.max_atten_db})
            for b in cfg.bands
        ]
    de: dict = {}
    if payload.deesser_enabled is not None:
        de["enabled"] = payload.deesser_enabled
    if payload.deesser_strength is not None:
        de["strength"] = payload.deesser_strength
    if de:
        update["deesser"] = cfg.deesser.model_copy(update=de)
    cfg = cfg.model_copy(update=update)
    try:
        report = DEQ.preview_into(src, out, language, cfg.resolved(language),
                                  payload.seconds, ai_config.loudness.sample_rate)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"EQ preview failed: {type(e).__name__}: {e}")
    return {"mp3_url": f"/api/cases/{case_id}/stories/{version_id}/voice"
                       f"/preview/{report['mp3']}",
            "report": report}


@app.get("/api/cases/{case_id}/stories/{version_id}/voice/preview/{filename}")
def story_voice_eq_preview_file(case_id: int, version_id: int, filename: str,
                                db: Session = Depends(get_db)):
    import re

    story = _story_or_404(db, case_id, version_id)
    if not re.fullmatch(r"eq_preview_[0-9a-f]{16}\.mp3", filename):
        raise HTTPException(status_code=404, detail="Unknown preview")
    path = _voice_dir(story) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/cases/{case_id}/story/latest")
def latest_story(case_id: int, db: Session = Depends(get_db)):
    # "Latest" for consumers means the best version, not the newest.
    story = (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id)
        .order_by(StoryVersion.is_best.desc(), StoryVersion.version.desc())
        .first()
    )
    if not story:
        raise HTTPException(status_code=404, detail="No story found")

    # Intentional: only the final story text.
    return {"story_text": story.story_text}


# ---------------------------------------------------------------------------
# Multilingual research metrics
# ---------------------------------------------------------------------------


@app.get("/api/cases/{case_id}/research/languages")
def research_languages(case_id: int, db: Session = Depends(get_db)):
    """Per-language research coverage: queries run, sources found, and how
    many canonical evidence items each language contributes to."""
    _get_case_or_404(db, case_id)
    sources = db.query(Source).filter(Source.case_id == case_id).all()
    facts = db.query(Fact).filter(Fact.case_id == case_id).all()
    src_by_id = {s.id: s for s in sources}

    queries: dict[str, list[str]] = {}
    lang_stats: dict[str, dict] = {}
    job = (
        db.query(ResearchJob)
        .filter(
            ResearchJob.case_id == case_id,
            ResearchJob.job_type == "research",
            ResearchJob.status == "completed",
        )
        .order_by(ResearchJob.completed_at.desc())
        .first()
    )
    if job:
        # Relational queries first (Part 24); legacy result_json
        # "languages" key is the fallback for older jobs.
        qrows = (
            db.query(ResearchQuery)
            .filter(ResearchQuery.research_job_id == job.id)
            .order_by(ResearchQuery.id)
            .all()
        )
        for q in qrows:
            queries.setdefault(q.language, []).append(q.query_text)
        if job.result_json:
            try:
                res = json.loads(job.result_json)
                if not queries:
                    for lang, info in (res.get("languages") or {}).items():
                        queries[lang] = info.get("queries") or []
                lang_stats = res.get("language_stats") or {}
            except (ValueError, TypeError):
                pass

    langs = sorted(
        set(ai_config.multilingual.research_languages)
        | {s.language for s in sources if s.language}
        | set(queries)
    )
    out = {}
    for lang in langs:
        lang_sources = [s for s in sources if s.language == lang]
        ids = {s.id for s in lang_sources}
        evidence = [
            f for f in facts
            if ids & set(json.loads(f.source_ids_json or "[]"))
        ]
        depth_counts = {"metadata_only": 0, "summary_only": 0,
                        "partial_text": 0, "full_text": 0, "unavailable": 0}
        for s in lang_sources:
            depth_counts[s.content_status or "summary_only"] = (
                depth_counts.get(s.content_status or "summary_only", 0) + 1
            )
        chunk_count = (
            db.query(SourceChunk)
            .filter(SourceChunk.source_id.in_(ids))
            .count()
            if ids else 0
        )
        st = lang_stats.get(lang) or {}
        out[lang] = {
            "searched": lang in queries or bool(lang_sources)
            or bool(st.get("queries")),
            "queries": queries.get(lang, []),
            "sources": len(lang_sources),
            "queries_run": st.get("queries"),
            "search_calls": st.get("search_calls"),
            "fetch_calls": st.get("fetch_calls"),
            "sources_found": st.get("sources_found"),
            "sources_accepted": st.get("sources_accepted"),
            "rejected": st.get("rejected"),
            "cost_usd": st.get("cost_usd"),
            "stop_reason": st.get("stop_reason"),
            **depth_counts,
            "full_text_sources": depth_counts["full_text"],
            "chunks": chunk_count,
            "source_families": len(
                {s.source_family for s in lang_sources if s.source_family}
            ),
            "evidence_items": len(evidence),
            "unique_evidence": sum(
                1
                for f in evidence
                if set(json.loads(f.source_ids_json or "[]")) <= ids
            ),
        }
    return {
        "canonical_language": ai_config.multilingual.canonical_language,
        "languages": out,
        "total_sources": len(sources),
    }


@app.get("/api/cases/{case_id}/research/capacity")
async def research_capacity(
    case_id: int,
    target_minutes: int = 45,
    db: Session = Depends(get_db),
):
    """Layered readiness: source depth, evidence extraction completeness,
    narrative capacity and the master-generation verdict — kept separate
    so 'capacity ready' can never masquerade as 'master ready'."""
    case = _get_case_or_404(db, case_id)
    provider_status = await _generation_provider_status()
    report = build_readiness(
        db, case.id, target_minutes, provider_status=provider_status
    )
    capacity = report["narrative_capacity"]
    # Flat capacity fields preserved for existing consumers; the layered
    # readiness objects are authoritative.
    return {**capacity, **report}


@app.get("/api/cases/{case_id}/research/depth")
async def research_depth(
    case_id: int,
    target_minutes: int = 45,
    db: Session = Depends(get_db),
):
    """Case-level research-depth panel: source depth, evidence depth,
    source families, chunk volume and the conservative capacity verdict."""
    case = _get_case_or_404(db, case_id)
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    contradictions = (
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    )
    source_ids = [s.id for s in sources]
    chunks = (
        db.query(SourceChunk)
        .filter(SourceChunk.source_id.in_(source_ids))
        .count()
        if source_ids else 0
    )
    by_status: dict[str, int] = {}
    for s in sources:
        k = s.content_status or "summary_only"
        by_status[k] = by_status.get(k, 0) + 1
    provider_status = await _generation_provider_status()
    report = build_readiness(
        db, case.id, target_minutes, provider_status=provider_status
    )
    capacity = report["narrative_capacity"]
    return {
        "sources": len(sources),
        "by_status": by_status,
        "independent_source_families": capacity["depth"][
            "independent_source_families"
        ],
        "chunks": chunks,
        "capacity": capacity,
        "gap_plan": research_gap_plan(
            facts, contradictions, sources, target_minutes
        )["missing"],
        "source_readiness": report["source_readiness"],
        "evidence_readiness": report["evidence_readiness"],
        "master_readiness": report["master_readiness"],
    }


@app.get("/api/cases/{case_id}/research/gaps")
def research_gaps(
    case_id: int,
    target_minutes: int = 45,
    db: Session = Depends(get_db),
):
    """Targeted ResearchGapPlan — what follow-up research should chase."""
    case = _get_case_or_404(db, case_id)
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    contradictions = (
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    )
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    return research_gap_plan(facts, contradictions, sources, target_minutes)


@app.post("/api/cases/{case_id}/research/followup")
async def research_followup(
    case_id: int,
    target_minutes: int = 45,
    db: Session = Depends(get_db),
):
    """Targeted follow-up (Part 15): focuses the next research
    pass on the identified evidence gaps rather than re-running broad
    research blindly."""
    from app.services.research_jobs import create_job

    case = _get_case_or_404(db, case_id)
    facts = db.query(Fact).filter(Fact.case_id == case.id).all()
    contradictions = (
        db.query(Contradiction).filter(Contradiction.case_id == case.id).all()
    )
    sources = db.query(Source).filter(Source.case_id == case.id).all()
    plan = research_gap_plan(facts, contradictions, sources, target_minutes)
    missing = plan["missing"]
    if not missing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "No research gaps identified — evidence depth is "
                           "sufficient for the requested duration.",
                "gap_plan": plan,
            },
        )

    provider = get_research_provider()
    if not provider.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Research engine not configured "
                   "(SearXNG backend unreachable)",
        )

    gap_lines = "; ".join(
        f"{m['type']} ({m['priority']}): {m['reason']}" for m in missing
    )
    objective = (
        "TARGETED follow-up research — do NOT repeat broad background "
        "research. Prior rounds found these evidence gaps: "
        f"{gap_lines}. Find primary and credible documentation that fills "
        "these specific gaps: official reports, public records, archives, "
        "interviews, transcripts, family/investigation documentation. "
        "Retrieve deep content (full text for public records, substantial "
        "verbatim excerpts elsewhere) — metadata-only sources do not help."
    )
    job = create_job(
        db,
        job_type="research",
        case_id=case.id,
        input_data={
            "case_title": case.canonical_title,
            "language": case.language,
            "follow_up": True,
            "gaps": missing,
        },
    )
    try:
        external_id = await provider.start_case_research(
            case_title=case.canonical_title,
            language=case.language,
            objective=objective,
            research_languages=ai_config.multilingual.research_languages,
        )
    except Exception as e:
        job.status = "failed"
        job.error = str(e)[:500]
        job.completed_at = utc_now()
        db.commit()
        raise HTTPException(status_code=502, detail=str(e))

    job.external_job_id = external_id
    job.status = "running"
    job.started_at = utc_now()
    db.commit()
    db.refresh(job)
    return {"job_id": job.id, "gap_plan": plan}


# ------------------------------------------------------------------
# Video research (Master_Prompt: multilingual transcript intelligence)
# ------------------------------------------------------------------


@app.post("/api/cases/{case_id}/video-research")
async def start_video_research(case_id: int, db: Session = Depends(get_db)):
    """Launch the multilingual video-research pipeline for a case.

    Runs in the background (video discovery + transcript acquisition can
    take far longer than a request timeout); the returned job is polled
    via /api/research-jobs/{job_id} like any other research job."""
    import asyncio

    from app.db.base import SessionLocal
    from app.services.video_research import run_video_research

    case = _get_case_or_404(db, case_id)
    job = research_jobs.create_job(
        db, job_type="video_research", case_id=case.id,
        input_data={"case_title": case.canonical_title},
    )

    async def _run(job_id: int):
        bg = SessionLocal()
        try:
            j = bg.get(ResearchJob, job_id)
            c = bg.get(Case, case_id)
            await run_video_research(bg, c, j)
        except Exception as e:  # pragma: no cover — defensive
            j = bg.get(ResearchJob, job_id)
            if j:
                j.status = "failed"
                j.error = str(e)[:500]
                j.completed_at = utc_now()
                bg.commit()
        finally:
            bg.close()

    spawn(_run(job.id), name=f"video_research:{job.id}")
    return {"job_id": job.id, "status": "queued"}


@app.get("/api/cases/{case_id}/videos")
def list_videos(case_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    videos = (
        db.query(VideoSource)
        .filter(VideoSource.case_id == case.id)
        .order_by(VideoSource.value_score.desc().nullslast(), VideoSource.id)
        .all()
    )
    return [_video_dict(v) for v in videos]


@app.get("/api/cases/{case_id}/videos/{video_id}")
def video_detail(case_id: int, video_id: int, db: Session = Depends(get_db)):
    case = _get_case_or_404(db, case_id)
    v = db.get(VideoSource, video_id)
    if not v or v.case_id != case.id:
        raise HTTPException(status_code=404, detail="Video not found")
    claims = (
        db.query(TranscriptClaim)
        .filter(TranscriptClaim.video_source_id == v.id)
        .order_by(TranscriptClaim.timestamp_start)
        .all()
    )
    segments = (
        db.query(TranscriptSegment)
        .filter(TranscriptSegment.video_source_id == v.id)
        .order_by(TranscriptSegment.segment_index)
        .all()
    )
    insights = (
        db.query(NarrativeInsight)
        .filter(NarrativeInsight.video_source_id == v.id)
        .all()
    )
    out = _video_dict(v)
    out.update(
        {
            "claims": [_claim_dict(c) for c in claims],
            "insights": [
                {
                    "id": i.id,
                    "type": i.insight_type,
                    "text_original": i.text_original,
                    "canonical_text_en": i.canonical_text_en,
                    "language": i.language,
                    "segment_ids": json.loads(i.segment_ids_json or "[]"),
                }
                for i in insights
            ],
            "segments": [
                {
                    "id": s.id,
                    "index": s.segment_index,
                    "start_seconds": s.start_seconds,
                    "end_seconds": s.end_seconds,
                    "text": s.text_original,
                    "canonical": s.canonical_text_en,
                }
                for s in segments
            ],
        }
    )
    return out


@app.get("/api/cases/{case_id}/claim-clusters")
def list_claim_clusters(case_id: int, db: Session = Depends(get_db)):
    """Cross-video claim clusters with member claims — the verification
    view (independent-family support, corroboration, contradictions)."""
    case = _get_case_or_404(db, case_id)
    clusters = (
        db.query(ClaimCluster)
        .filter(ClaimCluster.case_id == case.id)
        .order_by(ClaimCluster.independent_source_family_count.desc())
        .all()
    )
    claims_by_id = {
        c.id: c
        for c in db.query(TranscriptClaim)
        .filter(TranscriptClaim.case_id == case.id)
        .all()
    }
    out = []
    for cl in clusters:
        member_ids = json.loads(cl.member_claim_ids_json or "[]")
        members = [claims_by_id[i] for i in member_ids if i in claims_by_id]
        out.append(
            {
                "id": cl.id,
                "canonical_claim_en": cl.canonical_claim_en,
                "claim_type": cl.claim_type,
                "narrative_value": cl.narrative_value,
                "languages": json.loads(cl.languages_json or "[]"),
                "confidence": cl.confidence,
                "support_status": cl.support_status,
                "independent_source_family_count":
                    cl.independent_source_family_count,
                "member_count": len(members),
                "promoted_fact_id": cl.promoted_fact_id,
                "claims": [
                    {
                        **_claim_dict(m),
                        "video_source_id": m.video_source_id,
                        "video": (
                            m.video_source.video_id if m.video_source else None
                        ),
                        "channel": (
                            m.video_source.channel_name
                            if m.video_source else None
                        ),
                    }
                    for m in members
                ],
            }
        )
    return out


@app.get("/api/cases/{case_id}/dossier")
def case_dossier(case_id: int, db: Session = Depends(get_db)):
    """The CanonicalResearchDossier — the merged view of web research +
    verified video claims that feeds story generation."""
    from app.services.dossier import build_dossier

    case = _get_case_or_404(db, case_id)
    return build_dossier(db, case)


def _video_dict(v: VideoSource) -> dict:
    return {
        "id": v.id,
        "source_id": v.source_id,
        "video_id": v.video_id,
        "platform": v.platform,
        "url": v.url,
        "title": v.title,
        "channel_name": v.channel_name,
        "channel_url": v.channel_url,
        "language": v.language,
        "research_language": v.research_language,
        "duration_seconds": v.duration_seconds,
        "view_count": v.view_count,
        "published_at": v.published_at,
        "description": v.description,
        "discovery_query": v.discovery_query,
        "classification": v.classification,
        "source_independence": v.source_independence,
        "creator_source_family": v.creator_source_family,
        "duplicate_group": v.duplicate_group,
        "relevance_score": v.relevance_score,
        "novel_information_ratio": v.novel_information_ratio,
        "value_score": v.value_score,
        "transcript_status": v.transcript_status,
        "transcript_type": v.transcript_type,
        "segment_count": len(v.segments) if v.segments else 0,
        "claim_count": len(v.claims) if v.claims else 0,
        "processed_at": v.processed_at.isoformat() if v.processed_at else None,
    }


def _claim_dict(c: TranscriptClaim) -> dict:
    return {
        "id": c.id,
        "canonical_claim_en": c.canonical_claim_en,
        "original_claim": c.original_claim,
        "original_language": c.original_language,
        "claim_type": c.claim_type,
        "certainty": c.certainty,
        "verification_status": c.verification_status,
        "timestamp_start": c.timestamp_start,
        "timestamp_end": c.timestamp_end,
        "speaker": c.speaker,
        "quote_classification": c.quote_classification,
        "confidence": c.confidence,
        "cluster_id": c.cluster_id,
        "promoted_fact_id": c.promoted_fact_id,
        "segment_ids": json.loads(c.segment_ids_json or "[]"),
    }


@app.get("/api/cases/{case_id}/sources/{source_id}/chunks")
def source_chunks(case_id: int, source_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    source = (
        db.query(Source)
        .filter(Source.id == source_id, Source.case_id == case_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    chunks = (
        db.query(SourceChunk)
        .filter(SourceChunk.source_id == source.id)
        .order_by(SourceChunk.chunk_index)
        .all()
    )
    return [
        {
            "id": c.id,
            "chunk_index": c.chunk_index,
            "text": c.text,
            "token_count": c.token_count,
            "section_title": c.section_title,
            "page_or_location": c.page_or_location,
            "language": c.language,
        }
        for c in chunks
    ]


# ---------------------------------------------------------------------------
# Master story + localizations
# ---------------------------------------------------------------------------

def _best_master(db: Session, case_id: int) -> StoryVersion | None:
    return (
        db.query(StoryVersion)
        .filter(StoryVersion.case_id == case_id, StoryVersion.kind == "master")
        .order_by(StoryVersion.is_best.desc(), StoryVersion.version.desc())
        .first()
    )


@app.get("/api/cases/{case_id}/master-story")
def get_master_story(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    master = _best_master(db, case_id)
    if not master:
        raise HTTPException(status_code=404, detail="No master story found")
    localizations = (
        db.query(StoryVersion)
        .filter(
            StoryVersion.case_id == case_id,
            StoryVersion.kind == "localized",
            StoryVersion.master_version_id == master.id,
        )
        .order_by(StoryVersion.version.desc())
        .all()
    )
    return {
        "master": story_full_dict(master),
        "localizations": [story_meta_dict(s) for s in localizations],
    }


@app.post("/api/cases/{case_id}/master-story/generate")
async def generate_master_story(
    case_id: int,
    payload: GenerateStoryRequest,
    db: Session = Depends(get_db),
):
    """Generate the canonical English master. All localizations derive
    from a ready master — never directly from evidence."""
    case = _get_case_or_404(db, case_id)
    canonical = ai_config.multilingual.canonical_language

    # Layered preflight: provider → sources → evidence → capacity. Each
    # failure maps to its own code so the UI can say *why* generation is
    # blocked instead of showing a misleading "ready".
    provider_status = await _require_generation_authorized()
    report = build_readiness(
        db, case.id, payload.target_minutes,
        provider_status=provider_status or {},
    )
    master = report["master_readiness"]
    if master["status"] == "provider_blocked":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "provider_unauthorized",
                "provider_status": provider_status,
                "message": master["reason"],
            },
        )
    if master["status"] in ("incomplete_evidence", "failed"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evidence_incomplete",
                "message": (
                    "Evidence extraction is incomplete "
                    f"({master['reason']}). Re-run research once the "
                    "provider is healthy before generating."
                ),
                "evidence_readiness": report["evidence_readiness"],
                "capacity": report["narrative_capacity"],
            },
        )
    if master["status"] == "insufficient_research":
        capacity = report["narrative_capacity"]
        raise HTTPException(
            status_code=409,
            detail={
                "code": "insufficient_research",
                "message": (
                    "Evidence supports approximately "
                    f"{capacity['estimated_supported_minutes']} minutes — "
                    "more research is recommended for the requested duration."
                ),
                "capacity": capacity,
                "source_readiness": report["source_readiness"],
            },
        )

    try:
        story = await StoryPipeline(roles=MASTER_ROLES).run(
            db=db,
            case=case,
            target_minutes=payload.target_minutes,
            language=canonical,
            tone=payload.tone,
            iterations=payload.iterations,
            kind="master",
            words_per_minute=ai_config.words_per_minute_for(canonical),
        )
        return story_full_dict(story)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cases/{case_id}/localizations/generate")
async def generate_localization(
    case_id: int,
    language: str,
    target_minutes: int | None = None,
    db: Session = Depends(get_db),
):
    case = _get_case_or_404(db, case_id)
    if target_minutes is not None:
        try:
            check_target_minutes(target_minutes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    master = _best_master(db, case_id)
    if not master:
        raise HTTPException(status_code=404, detail="No master story found")
    try:
        story = await LocalizationPipeline().localize(
            db, case, master, language, target_minutes=target_minutes
        )
        return story_full_dict(story)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cases/{case_id}/localizations")
def list_localizations(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    rows = (
        db.query(StoryVersion)
        .filter(
            StoryVersion.case_id == case_id,
            StoryVersion.kind == "localized",
        )
        .order_by(StoryVersion.version.desc())
        .all()
    )
    return [story_meta_dict(s) for s in rows]


@app.get("/api/localizations/{version_id}")
def get_localization(version_id: int, db: Session = Depends(get_db)):
    story = db.get(StoryVersion, version_id)
    if not story or story.kind != "localized":
        raise HTTPException(status_code=404, detail="Localization not found")
    return story_full_dict(story)


@app.post("/api/localizations/{version_id}/improve")
async def improve_localization(version_id: int, db: Session = Depends(get_db)):
    """Produce a NEW localization version from the same master — prior
    versions are never overwritten."""
    story = db.get(StoryVersion, version_id)
    if not story or story.kind != "localized":
        raise HTTPException(status_code=404, detail="Localization not found")
    case = db.get(Case, story.case_id)
    master = db.get(StoryVersion, story.master_version_id)
    if not master:
        raise HTTPException(status_code=404, detail="Source master not found")
    try:
        new_version = await LocalizationPipeline().localize(
            db, case, master, story.language
        )
        return story_full_dict(new_version)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/localizations/{version_id}/compare-master")
def compare_master(version_id: int, db: Session = Depends(get_db)):
    """Side-by-side reviewer view: master text, localized text and the
    stored semantic-consistency report (internal evidence stays internal)."""
    story = db.get(StoryVersion, version_id)
    if not story or story.kind != "localized":
        raise HTTPException(status_code=404, detail="Localization not found")
    master = db.get(StoryVersion, story.master_version_id)
    critic = {}
    if story.critic_notes:
        try:
            critic = json.loads(story.critic_notes)
        except (ValueError, TypeError):
            critic = {}
    return {
        "localization": story_meta_dict(story),
        "localized_text": story.story_text,
        "master_text": master.story_text if master else None,
        "master_version": story.derived_from_master_version,
        "semantic_consistency": critic.get("semantic_consistency") or {},
        "native_quality": critic.get("native_quality") or {},
    }


# ---------------------------------------------------------------------------
# Agent runs
# ---------------------------------------------------------------------------


@app.get("/api/cases/{case_id}/agent-runs")
def case_agent_runs(case_id: int, db: Session = Depends(get_db)):
    _get_case_or_404(db, case_id)
    rows = (
        db.query(AgentRun)
        .filter(AgentRun.case_id == case_id)
        .order_by(AgentRun.started_at.desc())
        .all()
    )
    return [agent_run_dict(r) for r in rows]


@app.get("/api/agent-runs")
def all_agent_runs(db: Session = Depends(get_db)):
    rows = db.query(AgentRun).order_by(AgentRun.started_at.desc()).limit(100).all()
    out = []
    for r in rows:
        d = agent_run_dict(r)
        if r.case_id:
            case = db.get(Case, r.case_id)
            d["case_title"] = case.canonical_title if case else None
        else:
            d["case_title"] = None
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Database overview (internal view)
# ---------------------------------------------------------------------------


@app.get("/api/db/overview")
def db_overview(db: Session = Depends(get_db)):
    return {
        "cases": {
            "count": db.query(Case).count(),
            "items": [
                {
                    "id": c.id,
                    "title": c.canonical_title,
                    "status": c.status,
                    "created_at": c.created_at,
                }
                for c in db.query(Case).order_by(Case.created_at.desc()).limit(50).all()
            ],
        },
        "sources": {
            "count": db.query(Source).count(),
            "items": [
                {
                    "id": s.id,
                    "case_id": s.case_id,
                    "title": s.title,
                    "source_type": s.source_type,
                    "publisher": s.publisher,
                    "created_at": s.created_at,
                }
                for s in db.query(Source).order_by(Source.created_at.desc()).limit(50).all()
            ],
        },
        "facts": {
            "count": db.query(Fact).count(),
            "items": [
                {
                    "id": f.id,
                    "case_id": f.case_id,
                    "claim": f.claim[:200],
                    "category": f.category,
                    "confidence": f.confidence,
                }
                for f in db.query(Fact).order_by(Fact.id.desc()).limit(50).all()
            ],
        },
        "contradictions": {
            "count": db.query(Contradiction).count(),
            "items": [
                {
                    "id": c.id,
                    "case_id": c.case_id,
                    "topic": c.topic,
                    "severity": c.severity,
                }
                for c in db.query(Contradiction).order_by(Contradiction.id.desc()).limit(50).all()
            ],
        },
        "stories": {
            "count": db.query(StoryVersion).count(),
            "items": [
                {
                    "id": s.id,
                    "case_id": s.case_id,
                    "version": s.version,
                    "engagement_score": s.engagement_score,
                    "created_at": s.created_at,
                }
                for s in db.query(StoryVersion).order_by(StoryVersion.created_at.desc()).limit(50).all()
            ],
        },
        "discovery_history": {
            "count": db.query(DiscoveryCandidate).count(),
            "items": [
                {
                    "id": r.id,
                    "title": r.title,
                    "selected": r.selected,
                    "rejected": r.rejected,
                    "created_at": r.created_at,
                }
                for r in db.query(DiscoveryCandidate).order_by(DiscoveryCandidate.created_at.desc()).limit(50).all()
            ],
        },
        "agent_runs": {
            "count": db.query(AgentRun).count(),
        },
    }
