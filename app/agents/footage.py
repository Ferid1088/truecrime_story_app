"""The FootageAgent agent: its model calls, prompts and output checks run here;
the domain rules and storage helpers stay in app.documentary.visuals.footage."""

from __future__ import annotations

import re
import asyncio
from pathlib import Path
import json
import subprocess
import httpx
from sqlalchemy.orm import Session
from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T
from app.documentary.visuals import youtube_clips as YC
from app.documentary.visuals.research import next_asset_code
from app.documentary.visuals.footage import (
    DEDUPE_HAMMING,
    FootageCandidate,
    FootageError,
    FootageRejected,
    PROVIDERS,
    REQUIRE_RENDERABLE_RIGHTS,
    START_FRACTION,
    _meta_of,
    clip_window,
    download_video,
    extract_keyframe,
    frame_sheet,
    log,
    make_clip,
    probe_video,
    renderable,
    sheet_path,
)
from app.services.video_rights import RightsRefused, VideoRightsGate

MEDIA_SUFFIXES = (".mp4", ".webm", ".ogv", ".mov", ".m4v", ".mkv")


def staged(candidate, markers: list[str]) -> bool:
    """True when the video's own text says it is fiction or a stage show (a series, a film
    trailer, a play, a rehearsal): actors are never footage of the case."""
    text = " ".join(filter(None, [candidate.title, candidate.caption, candidate.credit])).lower()
    return any(m.lower() in text for m in markers)


def about_query(candidate, generic_words: list[str]) -> bool:
    """True when the video's own text (title, caption, credit) holds a distinctive word of the
    search it was found for ("Fall River" for "Fall River Massachusetts history"): a full-text
    hit on some other video's description is not footage of the subject."""
    words = {w for w in re.findall(r"\w{4,}", (candidate.found_for or "").lower())} - set(generic_words)
    if not words:
        return True
    text = " ".join(filter(None, [candidate.title, candidate.caption, candidate.credit])).lower()
    return any(w in text for w in words)


class FootageAgent:
    """Searches the footage providers for the given queries and adds
    muted clips to the case library (bounded by footage.max_clips_per_case)."""

    def __init__(self, client: httpx.AsyncClient | None, downloader=None):
        self.client = client
        self.cfg = ai_config.footage
        self.downloader = downloader

    def _http(self) -> httpx.AsyncClient:
        assert self.client is not None, "searching and downloading need an HTTP client"
        return self.client

    def providers(self, stopped: frozenset[str] = frozenset()) -> list:
        return [PROVIDERS[p](self._http()) for p in self.cfg.providers
               if p in PROVIDERS and p not in stopped]

    async def run(self, db: Session, case: Case, queries: list[dict],
                  found_during: str = "research", progress=None,
                  budget: int | None = None) -> dict:
        stats = {"queries": 0, "candidates": 0, "added": 0, "duplicates": 0, "uncut": 0,
                 "recut": 0, "rejected": 0, "rights_skipped": 0, "errors": 0,
                 "by_provider": {}}
        if not self.cfg.enabled:
            return {**stats, "skipped": "footage disabled"}
        if self.cfg.pieces:
            # videos kept earlier without pieces (the segmenter failed): cut
            # them by meaning now, before looking for new ones
            from app.documentary.visuals.pieces import cut_video, pieces_of

            for s in db.query(VisualAsset).filter(VisualAsset.case_id == case.id,
                                                  VisualAsset.asset_type == "video_source"):
                if not pieces_of(db, s) and await cut_video(db, case, s):
                    stats["recut"] += 1
        existing = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
        videos = [a for a in existing if a.asset_type in ("video", "video_source")]
        # a video cut into pieces counts once (its source)
        sources = [a for a in videos if a.asset_type == "video_source"
                   or "parent" not in (a.spec_json or "")]
        left = self.cfg.max_clips_per_case - len(sources)
        if budget is not None:
            left = min(left, budget)
        # identities already in the library: file URLs, and archive pages of clips
        seen = {a.source_url for a in existing if a.source_url}
        seen |= {a.page_url for a in videos if a.page_url}
        hashes = [a.phash for a in videos if a.phash]
        from app.documentary.visuals.research import adaptive_stop

        stopped = adaptive_stop(existing, ai_config.visual_search.adaptive_stop_min_sample,
                                ai_config.visual_search.adaptive_stop_min_share)
        stats["adaptive_stop"] = stopped
        providers = self.providers(frozenset(s["provider"] for s in stopped))
        for qi, q in enumerate(queries):
            if left <= 0:
                break
            stats["queries"] += 1
            ents = list(q.get("entities") or [])
            if q.get("entity") and q["entity"] not in ents:
                ents.append(q["entity"])
            for prov in providers:
                if left <= 0:
                    break
                for c in await prov.search(q["query"], self.cfg.max_candidates_per_query):
                    if left <= 0:
                        break
                    if staged(c, self.cfg.fiction_markers):
                        stats["rejected"] += 1
                        continue
                    c.entities, c.entity_key = list(ents), q.get("entity")
                    c.entity_type = q.get("entity_type")
                    c.query_kind = "context" if q.get("kind") == "context" else "exact"
                    stats["candidates"] += 1
                    if progress:
                        progress(qi / max(len(queries), 1),
                                 f"footage {prov.name}: {c.title or c.page_url}"[:120])
                    outcome = await self._consider(db, case, prov, c, seen, hashes, found_during)
                    stats[outcome] += 1
                    if outcome in ("added", "uncut"):   # both count toward the cap
                        left -= 1
                        stats["by_provider"][prov.name] = stats["by_provider"].get(prov.name, 0) + 1
        return stats

    async def ingest_film_candidates(self, db: Session, case: Case, profile: str | None = None,
                                     found_during: str = "research") -> dict:
        """Every research video of the case that is a film candidate and not yet in the library:
        ingested, or refused with its reason (the counts are reported, nothing is silent)."""
        from app.db.models import VideoSource

        stats: dict = {"added": 0, "skipped": 0, "errors": 0, "refused": {}}
        stored = {u for a in db.query(VisualAsset).filter(VisualAsset.case_id == case.id)
                  for u in (a.page_url, a.source_url) if u}
        for video in db.query(VideoSource).filter(VideoSource.case_id == case.id,
                                                  VideoSource.use == "film_candidate").order_by(VideoSource.id):
            if video.url in stored:
                stats["skipped"] += 1
                continue
            try:
                outcome = await self.ingest_film_candidate(db, case, video, profile, found_during)
            except RightsRefused as e:
                stats["refused"][str(e)] = stats["refused"].get(str(e), 0) + 1
                continue
            except (FootageError, OSError, subprocess.SubprocessError, IM.ImageError, httpx.HTTPError) as e:
                log.warning("film candidate %s failed: %s", video.url, e)
                stats["errors"] += 1
                continue
            if outcome in ("added", "uncut"):
                stats["added"] += 1
        return stats

    async def ingest_film_candidate(self, db: Session, case: Case, video, profile: str | None = None,
                                    found_during: str = "research") -> str:
        """A research video that is a film candidate goes through the same download, cutting and
        description as any footage, but only after the rights gate passed (use, rights status
        for the render profile, stored license evidence). Only a direct media file of an
        allowed host can be fetched; a page (a YouTube watch page) has no downloader."""
        if YC.is_youtube(video.url):
            return await self._ingest_youtube(db, case, video, profile, found_during)
        VideoRightsGate().require(video, profile)
        media = video.url if video.url.lower().split("?")[0].endswith(MEDIA_SUFFIXES) else None
        if media is None:
            raise RightsRefused("the video is a web page, not a media file; no downloader fetches it")
        candidate = FootageCandidate(
            provider="film_candidate", url=media, page_url=video.license_evidence_url,
            source_url=media, title=video.title, license=video.rights_status,
            credit=video.attribution_text, source_name=video.channel_name or "archive",
            found_for=video.discovery_query)
        return await self._ingest(db, case, candidate, video.rights_status,
                                  f"film candidate: {video.use_reason}", [], found_during)

    async def _ingest_youtube(self, db: Session, case: Case, video, profile: str | None,
                              found_during: str) -> str:
        """A YouTube video of an allowed source class (switch on): downloaded once, cut into
        pieces like any footage, with its source record and an on-screen credit."""
        YC.check_allowed(db, video, profile)
        tmp = storage.visuals_dir(case.id, "video") / f".{next_asset_code(db)}.yt"
        fetched = await (self.downloader or YC.YtDlpDownloader()).fetch(video.url, tmp)
        try:
            digest = YC.sha256_file(fetched)
            if db.query(VisualAsset).filter(VisualAsset.case_id == case.id, VisualAsset.sha256 == digest).first():
                return "duplicates"
            credit = f"{video.attribution_text or video.title} {ai_config.youtube_clips.credit_suffix}".strip()
            candidate = FootageCandidate(
                provider="youtube_clip", url=video.url, page_url=video.url, source_url=video.url,
                title=video.title, license=video.rights_status, credit=credit,
                source_name=video.channel_name or "YouTube", found_for=video.discovery_query,
                date=(video.published_at or "")[:20] or None)
            return await self._ingest_pieces(
                db, case, candidate, video.rights_status, f"youtube clip: {video.use_reason}", [], found_during,
                fetched=fetched, record=YC.source_record(video, digest))
        finally:
            fetched.unlink(missing_ok=True)

    async def _ingest_pieces(self, db, case, c: FootageCandidate, status: str, reason: str,
                             hashes: list[str], found_during: str, fetched: Path | None = None,
                             record: dict | None = None) -> str:
        """The video kept whole (muted) and cut into described pieces."""
        from app.documentary.visuals.pieces import ingest_video

        code = next_asset_code(db)
        tmp = fetched or storage.visuals_dir(case.id, "video") / f".{code}.download"
        try:
            if fetched is None:
                await download_video(c.url, self._http(), tmp,
                                     int(self.cfg.max_download_mb * 1024 * 1024))
            src = await asyncio.to_thread(probe_video, tmp)
            if src is None or not src["has_video"]:
                raise FootageRejected("unreadable video")
            if (src["height"] or 0) < self.cfg.min_height:
                return "rejected"
            # a long source keeps its part from 10 % in (past leaders and titles)
            duration = src["duration"] or c.duration or 0.0
            start = (duration * START_FRACTION
                     if duration > self.cfg.max_keep_seconds else 0.0)
            meta = _meta_of(c, status, reason, found_during)
            if record:
                meta["source_record"] = record
            source, pieces = await ingest_video(db, case, tmp, meta, start)
            if any(IM.hamming(source.phash, h) <= DEDUPE_HAMMING for h in hashes if h):
                # the same footage again: keep the library free of twins
                from app.documentary.visuals.pieces import remove_video

                remove_video(db, source)
                return "duplicates"
            hashes.append(source.phash)
            # kept even without pieces (the segmenter found nothing usable or
            # failed — recorded on the source, cut again on the next run or
            # by "Cut again"): never cut by the clock
            return "added" if pieces else "uncut"
        finally:
            if fetched is None:
                tmp.unlink(missing_ok=True)

    async def _consider(self, db, case, prov, c: FootageCandidate, seen: set[str],
                        hashes: list[str], found_during: str) -> str:
        if any(u in seen for u in (c.source_url, c.page_url, c.url) if u):
            return "duplicates"
        status, _ = R.classify(c.provider, c.license, c.url, c.page_url)
        if status == "do_not_use" or (REQUIRE_RENDERABLE_RIGHTS and not renderable(status)):
            return "rights_skipped"
        resolved = await prov.resolve(c)
        if resolved is None or not resolved.url:
            return "rejected"
        c = resolved
        if not about_query(c, self.cfg.generic_query_words):
            return "rejected"
        # the download host is checked too (blocked domains are never requested)
        status, reason = R.classify(c.provider, c.license, c.url, c.page_url)
        if status == "do_not_use" or (REQUIRE_RENDERABLE_RIGHTS and not renderable(status)):
            return "rights_skipped"
        seen.update(u for u in (c.source_url, c.page_url, c.url) if u)
        if ((c.duration and c.duration > self.cfg.max_source_seconds)
                or (c.height and c.height < self.cfg.min_height)
                or (c.size and c.size > self.cfg.max_download_mb * 1024 * 1024)):
            return "rejected"
        try:
            return await self._ingest(db, case, c, status, reason, hashes, found_during)
        except FootageRejected as e:
            log.info("footage %s rejected: %s", c.url, e)
            return "rejected"
        except FootageError as e:
            log.info("footage %s failed: %s", c.url, e)
            return "errors"
        except (OSError, subprocess.SubprocessError, IM.ImageError) as e:
            log.warning("footage %s failed: %s", c.url, e)
            return "errors"

    async def _ingest(self, db, case, c: FootageCandidate, status: str, reason: str,
                      hashes: list[str], found_during: str) -> str:
        if self.cfg.pieces:
            return await self._ingest_pieces(db, case, c, status, reason, hashes, found_during)
        cfg = self.cfg
        code = next_asset_code(db)
        vdir = storage.visuals_dir(case.id, "video")
        tmp, out, key = vdir / f".{code}.download", vdir / f"{code}.mp4", vdir / f".{code}.key.jpg"
        keep = False
        try:
            await download_video(c.url, self._http(), tmp, int(cfg.max_download_mb * 1024 * 1024))
            src = await asyncio.to_thread(probe_video, tmp)
            if src is None or not src["has_video"]:
                raise FootageRejected("unreadable video")
            duration = src["duration"] or c.duration or 0.0
            if not duration or duration > cfg.max_source_seconds:
                return "rejected"
            if (src["height"] or 0) < cfg.min_height:
                return "rejected"
            start, length = clip_window(duration, cfg.clip_seconds)
            await asyncio.to_thread(make_clip, tmp, out, start, length)
            clip = await asyncio.to_thread(probe_video, out)
            if clip is None or not clip["has_video"] or clip["has_audio"]:
                raise FootageError("ffmpeg clip unusable")
            clip_len = round(clip["duration"] or length, 3)
            await asyncio.to_thread(extract_keyframe, out, clip_len / 2, key)
            img = IM.open_image(key.read_bytes())
            ph = IM.dhash(img)
            if any(IM.hamming(ph, h) <= DEDUPE_HAMMING for h in hashes):
                return "duplicates"
            thumb = IM.save_thumbnail(img, storage.thumbs_dir(case.id) / f"{code}.jpg")
            await asyncio.to_thread(frame_sheet, out, 0.0, clip_len, sheet_path(case.id, code))
            tier, why = T.provisional_why(c.provider, "context", c.entity_type, c.query_kind)
            asset = VisualAsset(
                case_id=case.id, asset_code=code, asset_type="video",
                title=c.title[:500] if c.title else None, caption=c.caption,
                entities_json=json.dumps(c.entities, ensure_ascii=False),
                provider=c.provider, source_url=c.source_url or c.url, page_url=c.page_url,
                source_name=(c.source_name or "")[:300] or None,
                found_for=(c.found_for or "")[:300] or None,
                license=(c.license or "")[:200] or None, credit=(c.credit or "")[:500] or None,
                date_start=(c.date or "")[:20] or None,
                rights_status=status, rights_reason=reason,
                local_path=storage.rel(out), thumbnail_path=storage.rel(thumb),
                width=clip["width"], height=clip["height"], phash=ph, sha256=IM.sha256_file(out),
                duration_seconds=clip_len, has_original_audio=False,
                clip_start=0.0, clip_end=clip_len, asset_role="context",
                relevance_tier=tier, case_relevance=T.tier_label(tier),
                entity_key=c.entity_key, entity_type=c.entity_type, found_during=found_during,
                spec_json=json.dumps({
                    "media_url": c.url, "source_window": [start, round(start + length, 3)],
                    "source_duration": round(duration, 3), "source_had_audio": src["has_audio"],
                    "query_kind": c.query_kind, "tier_reason": why}),
            )
            db.add(asset)
            db.commit()
            hashes.append(ph)
            keep = True
            return "added"
        finally:
            tmp.unlink(missing_ok=True)
            key.unlink(missing_ok=True)
            if not keep:
                out.unlink(missing_ok=True)
                sheet_path(case.id, code).unlink(missing_ok=True)
