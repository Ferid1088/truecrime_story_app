"""Multilingual YouTube transcript research orchestrator (Parts 1/22/23).

CASE -> per-language video discovery -> ranking -> transcript acquisition
-> cleaning -> segmentation -> intelligence extraction -> dedup/clustering
-> independence analysis -> verification -> promotion into the canonical
Fact/Contradiction layer.

Discovery prefers the YouTube Data API when an API key is configured and
falls back to search-engine discovery (site:youtube.com queries
in each research language). A video is skipped only when it is not about
the case (relevance gate); there is no count cap and no novelty cutoff: a
video that only confirms known facts is still processed.
"""

import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.config import settings
from app.db.models import Case, ResearchJob, Source, VideoSource
from app.providers.transcript import get_transcript_provider
from app.services.tracking import record_run
from app.utils import utc_now
from app.services.video_rights import VideoUseClassifier

_YT_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?.*v=|shorts/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def video_id_from_url(url: str) -> str | None:
    m = _YT_ID.search(url or "")
    return m.group(1) if m else None


def _queries_for(case: Case, languages: list[str]) -> dict[str, str]:
    return {
        lang: ai_config.multilingual.video_query_stem(lang).format(
            title=case.canonical_title
        )
        for lang in languages
    }


def video_rank_score(candidate: dict) -> float:
    """Relevance plus the channel-type bonus from config: a documentary or
    court video outranks an unrelated news page for the same entity. A
    candidate with no relevance signal (YouTube API path) is neutral."""
    relevance = candidate.get("relevance")
    bonus = ai_config.youtube_research.classification_rank_bonus.get(
        candidate.get("classification") or "unknown", 0.0)
    return (0.5 if relevance is None else relevance) + bonus


def _log_skipped_video(db: Session, case: Case, candidate: dict, reason: str) -> None:
    """A video that is not transcribed stays visible with its reason."""
    row = db.query(VideoSource).filter(
        VideoSource.case_id == case.id, VideoSource.video_id == candidate["video_id"]
    ).first()
    if row is None:
        db.add(VideoSource(
            case_id=case.id, video_id=candidate["video_id"], url=candidate["url"],
            title=candidate["title"][:1000],
            channel_name=(candidate.get("channel_name") or None),
            language=(candidate.get("language") or "unknown")[:20],
            research_language=(candidate.get("research_language") or None),
            classification=(candidate.get("classification") or "unknown")[:60],
            relevance_score=candidate.get("relevance"),
            description=(candidate.get("description") or None),
            discovery_query=(candidate.get("discovery_query") or None),
            transcript_status="skipped", skip_reason=reason[:80],
            **VideoUseClassifier().classify(candidate).fields(),
        ))
    elif row.transcript_status == "skipped":
        row.skip_reason = reason[:80]


async def relevant_candidates(db: Session, case: Case, ranked: list[dict]) -> tuple[list[dict], int]:
    """The relevance gate: a score at or above `accept_from` passes, below
    `unrelated_below` is clearly unrelated, and the band in between is decided
    by a cheap model reading title, channel and description. Returns the
    relevant candidates in rank order and the number skipped (each logged
    with its reason)."""
    from app.agents.video_relevance import VideoRelevanceChecker

    gate = ai_config.youtube_research.relevance
    borderline = [c for c in ranked if c.get("relevance") is not None
                  and gate.unrelated_below <= c["relevance"] < gate.accept_from]
    verdicts = (await VideoRelevanceChecker().check(db, case.id, case.canonical_title, borderline)
                if borderline else {})
    kept: list[dict] = []
    skipped = 0
    for c in ranked:
        relevance = c.get("relevance")
        if relevance is not None and relevance < gate.unrelated_below:
            reason = "unrelated_to_case"
        elif c in borderline and not verdicts.get(c["video_id"], (True, ""))[0]:
            reason = f"not_relevant: {verdicts[c['video_id']][1]}"
        else:
            kept.append(c)
            continue
        _log_skipped_video(db, case, c, reason)
        skipped += 1
    db.commit()
    return kept, skipped


async def _discover_youtube_api(case: Case) -> list[dict]:
    """YouTube Data API path — cheap metadata discovery."""
    from app.services.youtube import YouTubeSearchService

    cfg = ai_config.youtube_research
    svc = YouTubeSearchService()
    out: list[dict] = []
    for lang, q in _queries_for(case, cfg.languages).items():
        try:
            items = await svc.search(q, max_results=cfg.max_results_per_language)
        except Exception:
            continue
        for it in items:
            vid = video_id_from_url(it.get("url") or "")
            if not vid:
                continue
            out.append(
                {
                    "video_id": vid,
                    "url": it["url"],
                    "title": it["title"],
                    "channel_name": it.get("channel"),
                    "description": it.get("description"),
                    "published_at": it.get("published_at"),
                    "language": lang,
                    "research_language": lang,
                    "discovery_query": q,
                }
            )
    return out


async def _discover_engine(db: Session, case: Case, job: ResearchJob) -> list[dict]:
    """TrueCrime Search Engine video-discovery — YouTube Data API or
    Brave Search discovery; no LLM performs discovery."""
    import asyncio

    from app.providers import get_research_provider

    provider = get_research_provider()
    if not provider.is_configured():
        return []
    cfg = ai_config.youtube_research
    external_id = await provider.start_video_discovery(
        case.canonical_title, cfg.languages
    )
    job.external_job_id = external_id
    db.commit()
    timeout = ai_config.research.job_timeout_minutes * 60
    waited = 0
    while waited < timeout:
        await asyncio.sleep(5)
        waited += 5
        pj = await provider.poll(external_id)
        if pj.status == "completed" and pj.result:
            vids = pj.result.get("videos") or []
            for v in vids:
                v.setdefault(
                    "discovery_query",
                    f"engine_video_discovery:{v.get('language') or 'unknown'}",
                )
                v["research_language"] = (
                    v.get("research_language") or v.get("language") or "unknown"
                )
                if not v.get("video_id"):
                    v["video_id"] = video_id_from_url(v.get("url") or "")
            return [v for v in vids if v.get("video_id")]
        if pj.status == "failed":
            raise RuntimeError(pj.error or "engine video discovery failed")
    raise RuntimeError("engine video discovery timed out")


def _corpus_key(video: VideoSource) -> str:
    """Content hash of the video's stored segment corpus — identical
    transcripts (re-runs, mirrored uploads) produce the same key and skip
    re-extraction (Part 39: cache by transcript hash)."""
    import hashlib

    dig = hashlib.sha256()
    for s in video.segments:
        dig.update(s.content_hash.encode())
    return dig.hexdigest()[:64]


async def _acquire_and_process(db: Session, case: Case, video: VideoSource) -> dict:
    """Transcript acquisition -> clean -> store -> normalize -> extract."""
    provider = get_transcript_provider()
    result = await provider.get_transcript(
        video.video_id,
        preferred_languages=[video.research_language or "en", "en"],
    )
    from app.services.transcripts import normalize_transcript, store_transcript

    stored = store_transcript(db, video, result)
    if not stored:
        return {"segments": 0, "claims": 0, "insights": 0}
    if (video.language or "en") != ai_config.multilingual.evidence_language:
        try:
            await normalize_transcript(db, video)
        except Exception as e:
            # Normalization failure is non-fatal — claims extract on the
            # original text; canonical_text_en stays NULL where untranslated.
            record_run(
                db, case.id, "Transcript Normalizer", "failed",
                input_summary=f"video={video.id}", error=str(e)[:300],
            )

    key = _corpus_key(video)
    if video.extraction_cache_key == key and video.claims:
        # Identical transcript already processed — reuse claims, zero
        # provider spend.
        video.transcript_status = "processed"
        video.processed_at = utc_now()
        db.flush()
        from app.db.models import NarrativeInsight, VisualLead

        return {
            "segments": stored,
            "claims": len(video.claims),
            "insights": db.query(NarrativeInsight)
            .filter(NarrativeInsight.video_source_id == video.id)
            .count(),
            "visual_leads": db.query(VisualLead)
            .filter(VisualLead.video_source_id == video.id)
            .count(),
            "cached": True,
        }

    from app.agents.transcript_intel import TranscriptIntelligenceAgent

    counts = await TranscriptIntelligenceAgent().extract(db, case, video)
    video.extraction_cache_key = key
    video.transcript_status = "processed"
    video.processed_at = utc_now()
    db.flush()
    return {"segments": stored, **counts}


class VideoDiscoveryError(RuntimeError):
    """The discovery ran but found no YouTube video at all."""


async def run_video_research(
    db: Session, case: Case, job: ResearchJob
) -> dict:
    """Full video-research pass for one case, recorded on `job`.

    Videos are processed in rank order; every relevant video is processed,
    however little new it adds (it is still film material).
    """
    cfg_yt = ai_config.youtube_research

    provider_mode = cfg_yt.discovery_provider
    if provider_mode == "auto":
        provider_mode = (
            "youtube_api" if settings.youtube_api_key else "engine"
        )

    job.status = "running"
    job.started_at = utc_now()
    db.commit()

    if provider_mode == "youtube_api":
        candidates = await _discover_youtube_api(case)
    else:
        candidates = await _discover_engine(db, case, job)

    if not candidates:
        raise VideoDiscoveryError(
            f"No YouTube video was found for '{case.canonical_title}' "
            f"in {', '.join(cfg_yt.languages)}.")

    # Dedupe against existing video rows AND within this discovery round —
    # canonical video identity (video_id) prevents double processing (P39).
    # A previously skipped video is reconsidered (the relevance gate may have changed).
    existing_ids = {
        v.video_id
        for v in db.query(VideoSource).filter(
            VideoSource.case_id == case.id, VideoSource.transcript_status != "skipped")
    }
    seen: set[str] = set()
    fresh: list[dict] = []
    for c in sorted(candidates, key=lambda c: -video_rank_score(c)):
        vid = c.get("video_id")
        if not vid or vid in existing_ids or vid in seen:
            continue
        seen.add(vid)
        fresh.append(c)
    unique, skipped = await relevant_candidates(db, case, fresh)

    stats = {
        "videos_discovered": len(candidates),
        "videos_new": len(unique),
        "videos_skipped": skipped,
        "videos_processed": 0,
        "transcripts_unavailable": 0,
        "claims": 0,
        "insights": 0,
    }

    for cand in unique:
        db.query(VideoSource).filter(
            VideoSource.case_id == case.id, VideoSource.video_id == cand["video_id"],
            VideoSource.transcript_status == "skipped").delete()
        # Canonical Source row — the video joins the source layer too.
        src = Source(
            case_id=case.id,
            title=cand["title"][:1000],
            url=cand["url"],
            source_type="youtube_video",
            language=(cand.get("language") or "unknown")[:20],
            publisher=(cand.get("channel_name") or None),
            summary=(cand.get("description") or "")[:2000] or None,
            content_status="metadata_only",
            retrieval_method="youtube_metadata",
            retrieved_at=utc_now(),
            research_provider=provider_mode,
            external_reference=job.external_job_id,
            status="active",
        )
        db.add(src)
        db.flush()
        video = VideoSource(
            case_id=case.id,
            source_id=src.id,
            video_id=cand["video_id"],
            url=cand["url"],
            title=cand["title"][:1000],
            channel_name=(cand.get("channel_name") or None),
            channel_url=(cand.get("channel_url") or None),
            language=(cand.get("language") or "unknown")[:20],
            published_at=(cand.get("published_at") or None),
            duration_seconds=cand.get("duration_seconds"),
            view_count=cand.get("view_count"),
            description=(cand.get("description") or None),
            discovery_query=(cand.get("discovery_query") or None),
            research_language=(cand.get("research_language") or None),
            classification=(cand.get("classification") or "unknown")[:60],
            relevance_score=cand.get("relevance"),
            transcript_status="pending",
            **VideoUseClassifier().classify(cand).fields(),
        )
        db.add(video)
        db.commit()

        # Cache: identical transcript already processed (P37/P39).
        try:
            out = await _acquire_and_process(db, case, video)
        except Exception as e:
            video.transcript_status = "failed"
            record_run(
                db, case.id, "Transcript Intelligence", "failed",
                input_summary=f"video={video.id}", error=str(e)[:300],
            )
            db.commit()
            continue
        if out["segments"] == 0:
            stats["transcripts_unavailable"] += 1
            db.commit()
            continue
        stats["videos_processed"] += 1
        stats["claims"] += out["claims"]
        stats["insights"] += out["insights"]

        db.commit()

    # Cross-video merge + verification + promotion (Parts 9/10/12).
    from app.services.claims import (
        cluster_claims,
        compute_independence,
        promote_clusters,
        score_videos,
        verify_clusters,
    )

    cl = await cluster_claims(db, case)
    indep = compute_independence(db, case)
    ver = await verify_clusters(db, case)
    prom = promote_clusters(db, case)
    score_videos(db, case)

    stats.update(
        {
            "clusters": cl["clusters"],
            "verified_clusters": ver["verified"],
            "promoted_facts": prom["promoted"],
            "promoted_contradictions": prom["contradictions"],
            "independence": indep,
        }
    )
    job.result_summary = (
        f"videos={stats['videos_new']} skipped={stats['videos_skipped']} processed={stats['videos_processed']} "
        f"unavailable={stats['transcripts_unavailable']} "
        f"claims={stats['claims']} clusters={cl['clusters']} "
        f"promoted={prom['promoted']}"
    )[:500]
    job.status = "completed"
    job.current_stage = "completed"
    job.completed_at = utc_now()
    db.commit()
    return stats
