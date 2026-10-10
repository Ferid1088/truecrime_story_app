import json

from sqlalchemy.orm import Session

from app.db.models import (
    Source,
    Fact,
    Contradiction,
    StoryVersion,
    AgentRun,
    VideoSource,
    TranscriptClaim,
)
from app.agents.story import (
    structure_without_text,
)




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


def case_stats(db: Session, case_id: int) -> dict:
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
        "generation_usage": generation_usage(db, case_id),
    }


def generation_usage(db: Session, case_id: int) -> dict:
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


def video_dict(v: VideoSource) -> dict:
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


def claim_dict(c: TranscriptClaim) -> dict:
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
