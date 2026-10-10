"""Multilingual YouTube transcript research orchestrator (Parts 1/22/23).

CASE -> per-language video discovery -> ranking -> transcript acquisition
-> cleaning -> segmentation -> intelligence extraction -> dedup/clustering
-> independence analysis -> verification -> promotion into the canonical
Fact/Contradiction layer.

Discovery prefers the YouTube Data API when an API key is configured and
falls back to search-engine discovery (site:youtube.com queries
in each research language). Stop conditions bound cost:
max transcripts per case, minimum novel-information ratio, and a
consecutive-low-value cutoff.
"""

import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.config import settings
from app.db.models import Case, ResearchJob, Source, VideoSource
from app.providers.transcript import get_transcript_provider
from app.services.tracking import record_run
from app.utils import utc_now

_YT_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?.*v=|shorts/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def video_id_from_url(url: str) -> str | None:
    m = _YT_ID.search(url or "")
    return m.group(1) if m else None


# Native query stems per language — never a literal translation of one
# English query (Part 1).
_QUERY_STEMS = {
    "en": "{title} documentary investigation",
    "de": "{title} Doku Fall Ermittlungen",
    "fa": "{title} مستند پرونده تحقیق",
    "ar": "{title} وثائقي قضية تحقيق",
}


def _queries_for(case: Case, languages: list[str]) -> dict[str, str]:
    return {
        lang: _QUERY_STEMS.get(lang, "{title} documentary").format(
            title=case.canonical_title
        )
        for lang in languages
    }


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
    SearXNG site-restricted search; no LLM performs discovery."""
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
    if (video.language or "en") != ai_config.multilingual.canonical_language:
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
        from app.db.models import NarrativeInsight

        return {
            "segments": stored,
            "claims": len(video.claims),
            "insights": db.query(NarrativeInsight)
            .filter(NarrativeInsight.video_source_id == video.id)
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


async def run_video_research(
    db: Session, case: Case, job: ResearchJob
) -> dict:
    """Full video-research pass for one case, recorded on `job`.

    Order matters: process videos in rank order and stop when the marginal
    novel-information ratio drops below the configured floor for
    `consecutive_low_value_stop` videos in a row (Part 22).
    """
    cfg_yt = ai_config.youtube_research
    cfg_stop = ai_config.research_stop_conditions

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

    # Dedupe against existing video rows AND within this discovery round —
    # canonical video identity (video_id) prevents double processing (P39).
    existing_ids = {
        v.video_id
        for v in db.query(VideoSource).filter(VideoSource.case_id == case.id)
    }
    seen: set[str] = set()
    unique: list[dict] = []
    per_lang: dict[str, int] = {}
    for c in sorted(
        candidates, key=lambda c: -(c.get("relevance") or 0.0)
    ):
        vid = c.get("video_id")
        lang = c.get("research_language") or c.get("language") or "unknown"
        if not vid or vid in existing_ids or vid in seen:
            continue
        if per_lang.get(lang, 0) >= cfg_yt.max_videos_per_language:
            continue
        seen.add(vid)
        per_lang[lang] = per_lang.get(lang, 0) + 1
        unique.append(c)

    stats = {
        "videos_discovered": len(candidates),
        "videos_new": len(unique),
        "videos_processed": 0,
        "transcripts_unavailable": 0,
        "claims": 0,
        "insights": 0,
        "stop_reason": None,
    }

    processed = db.query(VideoSource).filter(
        VideoSource.case_id == case.id,
        VideoSource.transcript_status == "processed",
    ).count()
    low_value_streak = 0

    for cand in unique:
        if processed >= cfg_stop.max_transcripts_per_case:
            stats["stop_reason"] = "max_transcripts_per_case"
            break
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
        processed += 1
        stats["videos_processed"] += 1
        stats["claims"] += out["claims"]
        stats["insights"] += out["insights"]

        # Incremental novelty check for the stop condition.
        from app.services.claims import cluster_claims, score_videos

        await cluster_claims(db, case)
        scores = score_videos(db, case)
        novel = scores.get(video.id, {}).get("novel", 0.0)
        if novel < cfg_stop.minimum_novel_information_ratio:
            low_value_streak += 1
            if low_value_streak >= cfg_stop.consecutive_low_value_stop:
                stats["stop_reason"] = "consecutive_low_value"
                break
        else:
            low_value_streak = 0
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
        f"videos={stats['videos_new']} processed={stats['videos_processed']} "
        f"unavailable={stats['transcripts_unavailable']} "
        f"claims={stats['claims']} clusters={cl['clusters']} "
        f"promoted={prom['promoted']} stop={stats['stop_reason'] or 'complete'}"
    )[:500]
    job.status = "completed"
    job.current_stage = "completed"
    job.completed_at = utc_now()
    db.commit()
    return stats
