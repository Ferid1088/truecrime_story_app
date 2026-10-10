
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import (
    Case,
    Source,
    Fact,
    Contradiction,
    StoryVersion,
    DiscoveryCandidate,
    AgentRun,
)
from app.core.config import settings
from app.core.ai_config import ai_config


from app.lifecycle.api import resolution_dict
from app.api.deps import get_case_or_404
from app.api.serializers import agent_run_dict, case_stats
from app.api import deps

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/api/agents")
def list_agents():
    """The agent directory: name, model role and alias, prompt file, status."""
    from app.agents.registry import catalog

    return catalog()


@router.get("/api/settings/status")
def settings_status(db: Session = Depends(get_db)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    gen_provider = deps.generation_provider()
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
            "configured": deps.research_provider().is_configured(),
        },
        "youtube": {"configured": bool(settings.youtube_api_key)},
        "research_provider": {
            "provider": deps.research_provider().name,
            "configured": deps.research_provider().is_configured(),
        },
        "multilingual": {
            "research_languages": ai_config.multilingual.research_languages,
            "canonical_language": ai_config.multilingual.canonical_language,
            "localization_languages": ai_config.multilingual.localization_languages,
        },
        "database": {"connected": db_ok, "url": settings.database_url.split("///")[0]},
    }


@router.get("/api/integrations/research/status")
async def research_status():
    """TrueCrime Search Engine status — SearXNG backend, fetcher,
    embedding and LLM-intelligence health. No OpenRouter."""
    provider = deps.research_provider()
    if hasattr(provider, "provider_status"):
        return await provider.provider_status()
    configured = provider.is_configured()
    return {
        "provider": provider.name,
        "configured": configured,
        "reachable": await provider.check_health() if configured else False,
    }


@router.get("/api/integrations/search-engine/status")
async def search_engine_status():
    """Alias for the research status — explicit engine-scoped name."""
    return await research_status()


@router.get("/api/integrations/apimaster/status")
async def apimaster_status():
    """APIMaster = generation only (Part 8): classified status plus
    per-alias model availability from GET /models."""
    status = await deps.generation_provider_status()
    gen_cfg = ai_config.generation_provider()
    return {
        **status,
        "models": gen_cfg.models,
        "routing": gen_cfg.routing,
        "fallbacks": gen_cfg.fallbacks,
    }


@router.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    cases = db.query(Case).order_by(Case.created_at.desc()).all()

    def count_status(*statuses: str) -> int:
        return sum(1 for c in cases if c.status in statuses)

    recent = []
    for c in cases[:10]:
        stats = case_stats(db, c.id)
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
        "follow_up_candidates": pending_follow_ups(db),
        "resolution_counts": {
            s: sum(1 for c in cases if (c.resolution_status or "UNKNOWN") == s)
            for s in ("SOLVED", "UNSOLVED", "UNKNOWN", "STATUS_UNDER_REVIEW")},
    }


def pending_follow_ups(db: Session) -> list[dict]:
    from app.db.models import FollowUpCandidate
    from app.lifecycle.followups import candidate_dict

    rows = (db.query(FollowUpCandidate).filter(FollowUpCandidate.state == "pending")
            .order_by(FollowUpCandidate.id.desc()).all())
    return [candidate_dict(db, fu) for fu in rows]


@router.get("/api/db/overview")
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


@router.get("/api/cases/{case_id}/agent-runs")
def case_agent_runs(case_id: int, db: Session = Depends(get_db)):
    get_case_or_404(db, case_id)
    rows = (
        db.query(AgentRun)
        .filter(AgentRun.case_id == case_id)
        .order_by(AgentRun.started_at.desc())
        .all()
    )
    return [agent_run_dict(r) for r in rows]


@router.get("/api/agent-runs")
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
