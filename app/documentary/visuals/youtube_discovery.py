"""YouTube video discovery through the Data API (P15): finding and ranking only — this module
never downloads a video (the separate, owner-switched "YouTube clip downloader" of
app/documentary/visuals/youtube_clips.py is untouched and stays off by default).

Cheap calls first: a known channel's uploads are read via playlistItems.list (~1 unit/page,
cached) and filtered by title instead of search.list; up to 50 videos get their details
(snippet + the real license + duration) in one videos.list call (1 unit); search.list runs
only where the cheap path found nothing, and only within the discovery share and the API's own
separate daily search-call cap (app.documentary.visuals.youtube_quota). Every call is also
cached (query_key, config cache_expiry_hours) so the same query is never run twice.

Channel type: the existing classifier (app.services.video_rights.VideoUseClassifier) decides,
now fed the real API license (P15b) — never duplicated here.

Moment alignment (P15c): the transcript is read the way the app already does today
(app.providers.transcript.youtube.YouTubeTranscriptProvider, the `youtube_transcript_api`
library) — confirmed NOT to use YouTube Data API quota (its own `retrieval_method` names the
library, not an API endpoint). A sentence is matched to the transcript segment(s) sharing the
most significant words, with a margin; candidates are ranked by that match quality. Checking
three frames of the moment (the task's own ask) is NOT implemented here: doing so would need
either downloading (forbidden in this row) or a frame-preview mechanism this project does not
yet have — recorded as an open gap, not worked around.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re

import httpx

from app.core.ai_config import ai_config
from app.documentary.visuals import youtube_quota as Q
from app.services.video_rights import VideoUseClassifier

API = "https://www.googleapis.com/youtube/v3"


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:40]


async def _get(client: httpx.AsyncClient, db, case_id: int | None, stage: str, endpoint: str,
               params: dict, api_key: str) -> dict | None:
    qk = _key(endpoint, sorted(params.items()))
    cached = Q.cached_result(db, qk)
    if cached is not None:
        return cached
    r = await client.get(f"{API}/{endpoint}", params={**params, "key": api_key})
    if r.status_code != 200:
        return None
    data = r.json()
    Q.record_call(db, stage=stage, endpoint=f"{endpoint}.list", case_id=case_id)
    Q.store_cache(db, stage=stage, endpoint=f"{endpoint}.list", query_key=qk, result=data, case_id=case_id)
    return data


async def uploads_playlist_id(client: httpx.AsyncClient, db, case_id: int | None,
                              channel_id: str, api_key: str, stage: str = "discovery") -> str | None:
    """1 unit (channels.list), cached: the channel's "uploads" playlist id."""
    data = await _get(client, db, case_id, stage, "channels",
                      {"part": "contentDetails", "id": channel_id}, api_key)
    items = (data or {}).get("items") or []
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


async def channel_uploads(client: httpx.AsyncClient, db, case_id: int | None, channel_id: str,
                          api_key: str, stage: str = "discovery", max_pages: int = 1) -> list[dict]:
    """playlistItems.list (~1 unit/page, cached): cheap listing of a known channel's uploads,
    to be filtered by title — never search.list for a channel we already know."""
    cfg = ai_config.youtube_data_api
    playlist_id = await uploads_playlist_id(client, db, case_id, channel_id, api_key, stage)
    if playlist_id is None:
        return []
    out: list[dict] = []
    page_token = None
    for _ in range(max_pages):
        params = {"part": "snippet", "playlistId": playlist_id,
                 "maxResults": cfg.max_videos_per_channel_page}
        if page_token:
            params["pageToken"] = page_token
        data = await _get(client, db, case_id, stage, "playlistItems", params, api_key)
        if not data:
            break
        out += data.get("items") or []
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return out


def filter_by_title(items: list[dict], must_name: list[str]) -> list[dict]:
    """The cheap pre-filter (P5's own rule, reused here): the upload's own title must name one
    of the given words, else it is dropped before a single videos.list unit is spent on it."""
    words = {w for n in must_name for w in re.findall(r"\w{4,}", n.lower())}
    if not words:
        return items
    out = []
    for it in items:
        title = str((it.get("snippet") or {}).get("title") or "").lower()
        if any(w in title for w in words):
            out.append(it)
    return out


async def video_details(client: httpx.AsyncClient, db, case_id: int | None, video_ids: list[str],
                        api_key: str, stage: str = "discovery") -> list[dict]:
    """videos.list, batched up to max_video_ids_per_call (1 unit per batch): snippet, the real
    license (status.license) and duration for up to 50 videos at once."""
    cfg = ai_config.youtube_data_api
    out: list[dict] = []
    for i in range(0, len(video_ids), cfg.max_video_ids_per_call):
        batch = video_ids[i:i + cfg.max_video_ids_per_call]
        if not batch:
            continue
        data = await _get(client, db, case_id, stage, "videos",
                          {"part": "snippet,contentDetails,status", "id": ",".join(batch)}, api_key)
        out += (data or {}).get("items") or []
    return out


async def search_videos(client: httpx.AsyncClient, db, case_id: int | None, query: str,
                        api_key: str, stage: str = "discovery", max_results: int = 10) -> list[dict] | None:
    """search.list — only where the cheap path found nothing. Gated by the discovery share
    (when stage == "discovery") AND the API's own separate daily search-call cap; returns None
    (never an empty list) when the budget is the reason nothing was searched, so a caller can
    tell "found nothing" apart from "did not even try"."""
    cfg = ai_config.youtube_data_api
    if stage == "discovery" and not Q.discovery_status(db)["available"]:
        return None
    if Q.search_calls_used_today(db, stage=stage) >= cfg.daily_search_calls:
        return None
    data = await _get(client, db, case_id, stage, "search",
                      {"part": "snippet", "type": "video", "q": query, "maxResults": max_results}, api_key)
    return (data or {}).get("items") or []


def classify_video(item: dict, classification: str | None = None) -> dict:
    """The existing classifier (never duplicated), fed the real API license (P15b)."""
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    candidate = {
        "url": f"https://www.youtube.com/watch?v={item.get('id')}",
        "title": snippet.get("title"), "description": snippet.get("description"),
        "channel_name": snippet.get("channelTitle"), "classification": classification,
        "license": status.get("license"),
    }
    use = VideoUseClassifier().classify(candidate)
    return {**candidate, **use.fields(), "video_id": item.get("id"),
           "duration_seconds": _iso8601_seconds((item.get("contentDetails") or {}).get("duration"))}


def _iso8601_seconds(duration: str | None) -> int | None:
    if not duration:
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return None
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def align_sentence(segments: list, sentence: str, margin: float) -> dict | None:
    """P15c: the transcript segment(s) sharing the most significant words with `sentence`, as a
    time range with `margin` applied, and a 0-1 match score. None when nothing overlaps."""
    words = set(re.findall(r"\w{4,}", sentence.lower()))
    if not words or not segments:
        return None
    best = None
    for seg in segments:
        # provider segments carry .text; stored TranscriptSegment rows carry .text_original
        text = str(getattr(seg, "text", None) or getattr(seg, "text_original", None)
                   or getattr(seg, "canonical_text_en", "") or "")
        seg_words = set(re.findall(r"\w{4,}", text.lower()))
        overlap = len(words & seg_words)
        if overlap and (best is None or overlap > best[0]):
            best = (overlap, seg)
    if best is None:
        return None
    overlap, seg = best
    return {"start": round(max(0.0, seg.start_seconds - margin), 2),
           "end": round(seg.end_seconds + margin, 2),
           "score": round(overlap / len(words), 4)}


async def candidates_for_sentence(client: httpx.AsyncClient, db, case_id: int | None,
                                  sentence: str, query: str, api_key: str,
                                  channel_id: str | None = None,
                                  must_name: list[str] | None = None,
                                  transcript_provider=None) -> dict:
    """The full P15 chain for one sentence: cheap channel uploads first when a known channel is
    given, else a budgeted search.list; videos.list for details; the existing classifier fed
    the real license; transcript alignment (no Data API quota) for a time range with a margin;
    ranked, never downloaded. {"candidates": [...], "searched": bool, "status_message": str|None}
    — "searched" is False only when a channel's own uploads answered the sentence and no
    search.list call was needed at all."""
    cfg = ai_config.youtube_data_api
    searched = False
    items: list[dict] = []
    if channel_id:
        uploads = await channel_uploads(client, db, case_id, channel_id, api_key)
        items = filter_by_title(uploads, must_name or [])
    if not items:
        found = await search_videos(client, db, case_id, query, api_key)
        searched = True
        if found is None:
            return {"candidates": [], "searched": True,
                   "status_message": Q.discovery_status(db)["status_message"]}
        items = found
    video_ids = [v for v in (_video_id_of(it) for it in items) if v is not None]
    details = await video_details(client, db, case_id, video_ids, api_key)
    candidates = [classify_video(d) for d in details]
    for c in candidates:
        c["alignment"] = await _alignment_for(c["video_id"], sentence, cfg.clip_margin_seconds,
                                              transcript_provider)
    aligned = [c for c in candidates if c["alignment"]]
    ranked = rank_candidates(aligned or candidates, cfg.max_candidates_per_sentence)
    return {"candidates": ranked, "searched": searched, "status_message": None}


def _video_id_of(item: dict) -> str | None:
    """A playlistItems.list item names its video at snippet.resourceId.videoId; a
    search.list item names it at id.videoId — the two list endpoints do not share a shape."""
    vid = (item.get("id") or {})
    if isinstance(vid, dict) and vid.get("videoId"):
        return vid["videoId"]
    if isinstance(vid, str):
        return vid
    return ((item.get("snippet") or {}).get("resourceId") or {}).get("videoId")


async def _alignment_for(video_id: str, sentence: str, margin: float, provider=None) -> dict | None:
    if provider is None:
        from app.providers.transcript.youtube import YouTubeTranscriptProvider

        provider = YouTubeTranscriptProvider()
    result = await provider.get_transcript(video_id)
    if not result.available:
        return None
    return align_sentence(result.segments, sentence, margin)


# ---------------------------------------------------------------------------
# clip discovery into the film pipeline (V2): queries -> classified VideoSource
# ---------------------------------------------------------------------------


def upsert_discovered(db, case, candidate: dict, query: str):
    """Store (or refresh) one discovered video as a VideoSource. The existing row keeps its
    transcript state and research history; the new fields — the real channel, the real API
    license, duration and the classifier's verdict — are always refreshed. Returns the row."""
    from app.db.models import VideoSource

    fields = {
        "url": candidate["url"], "title": (candidate.get("title") or "")[:1000],
        "channel_name": (candidate.get("channel_name") or None),
        "channel_url": (
            f"https://www.youtube.com/channel/{candidate['channel_id']}"
            if candidate.get("channel_id") else None),
        "description": (candidate.get("description") or None),
        "duration_seconds": candidate.get("duration_seconds"),
        "youtube_license": candidate.get("license"),
        "discovery_query": query[:500],
    }
    fields.update({k: v for k, v in candidate.items()
                   if k in ("use", "rights_status", "use_reason", "license_evidence_url",
                            "license_checked_at", "attribution_text", "source_class",
                            "class_evidence")})
    proof = candidate.get("channel_proof")
    if isinstance(proof, dict) and proof.get("status") == "proven":
        fields["channel_proof_json"] = json.dumps(proof, ensure_ascii=False)
    row = db.query(VideoSource).filter(
        VideoSource.case_id == case.id,
        VideoSource.video_id == candidate["video_id"]).first()
    if row is not None:
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()
        return row
    row = VideoSource(case_id=case.id, video_id=candidate["video_id"],
                      platform="youtube", classification="unknown",
                      transcript_status="discovered", **fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


async def _fetch_and_store_transcript(db, video, transcript_provider=None) -> bool:
    """A film candidate's captions (no Data API quota) are stored like every researched
    video's, so sentence alignment and the research corpus can use them."""
    from app.services.transcripts import store_transcript

    if transcript_provider is None:
        from app.providers.transcript.youtube import YouTubeTranscriptProvider

        transcript_provider = YouTubeTranscriptProvider()
    result = await transcript_provider.get_transcript(video.video_id)
    return store_transcript(db, video, result) > 0


async def discover_for_case(client: httpx.AsyncClient, db, case, queries: list[str],
                            api_key: str, stage: str = "discovery",
                            transcript_provider=None) -> dict:
    """The V2 bridge: the case's footage queries become stored clip candidates through the
    Data API — budgeted search.list, one batched videos.list for the real license/channel,
    the existing classifier decides, VideoSource rows dedupe by video id. A film candidate's
    transcript is fetched and stored too (no Data API quota) so sentence alignment works.
    Never downloads a video — that stays the separate, owner-switched clip downloader's job."""
    cfg = ai_config.youtube_data_api
    stats = {"queries": 0, "searched": 0, "items": 0, "classified": 0,
             "film_candidates": 0, "new": 0, "updated": 0, "transcripts": 0,
             "quota_stopped": False}
    seen: set[str] = set()
    for query in queries[: cfg.discovery_max_queries_per_case]:
        stats["queries"] += 1
        found = await search_videos(client, db, case.id, query, api_key, stage=stage,
                                    max_results=cfg.discovery_max_results)
        if found is None:
            stats["quota_stopped"] = True
            break
        stats["searched"] += 1
        ids = [v for v in (_video_id_of(i) for i in found) if v and v not in seen]
        seen.update(ids)
        stats["items"] += len(ids)
        details = await video_details(client, db, case.id, ids, api_key, stage=stage)
        for item in details:
            snippet = item.get("snippet") or {}
            candidate = classify_video(item)
            candidate["channel_id"] = snippet.get("channelId")
            stats["classified"] += 1
            from app.db.models import VideoSource

            created = db.query(VideoSource).filter_by(
                case_id=case.id, video_id=candidate["video_id"]).first() is None
            row = upsert_discovered(db, case, candidate, query)
            stats["new" if created else "updated"] += 1
            if candidate.get("use") == "film_candidate":
                stats["film_candidates"] += 1
                # captions are a help for sentence alignment, not a gate
                with contextlib.suppress(Exception):
                    stats["transcripts"] += 1 if await _fetch_and_store_transcript(
                        db, row, transcript_provider) else 0
    return stats


def rank_candidates(candidates: list[dict], limit: int) -> list[dict]:
    """Best first: a real alignment score first, then the channel-type bonus (reused from
    youtube_research.classification_rank_bonus), official/witness material before an unaligned
    or research-only one."""
    bonus = ai_config.youtube_research.classification_rank_bonus

    def score(c: dict) -> float:
        align = (c.get("alignment") or {}).get("score", 0.0)
        return align + bonus.get(c.get("source_class") or "", 0.0) + (0.0 if c["use"] == "film_candidate" else -0.1)

    return sorted(candidates, key=score, reverse=True)[:limit]
