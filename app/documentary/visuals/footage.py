"""Case-related footage (Master task §8.3): real moving pictures from
rights-known archives — Wikimedia Commons video and the Internet Archive —
stored as a short, MUTED H.264 clip plus a keyframe for the vision check.

Why muted: the film's sound is the narration and the director's music
and silence. Archive sound under narration would compete with it (and
often carries rights of its own), so the stored clip has no audio stream
at all and the renderer never has anything to leak.

Per candidate, cheap checks come first so nothing big is downloaded for
nothing:
  rights (license metadata, blocked domains) -> length/height/size from
  the provider's metadata -> streamed download with a size cap -> ffprobe
  -> clip window (10 % in, up to footage.clip_seconds) -> muted 25 fps
  H.264 MP4, max 1920x1080, even dimensions -> keyframe JPEG at the
  window's middle (thumbnail_path) -> VisualAsset(asset_type="video").

Providers (config footage.providers):
  wikimedia_video  — Commons search with filetype:video; license from
                     extmetadata; a transcoded derivative near 720p is
                     downloaded instead of a huge original.
  internet_archive — advancedsearch (mediatype:movies) for the item and
                     its licenseurl, then the item's metadata for one
                     MP4/OGV file under the size cap.
No yt-dlp: only direct file URLs of archives that state their rights.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import rights as R
from app.documentary.visuals import tiers as T
from app.documentary.visuals.research import _clean, next_asset_code

log = logging.getLogger(__name__)

# Internet Archive item endpoints (the search endpoint is config:
# footage.internet_archive_api).
IA_METADATA_URL = "https://archive.org/metadata/{identifier}"
IA_DOWNLOAD_URL = "https://archive.org/download/{identifier}/{name}"
IA_DETAILS_URL = "https://archive.org/details/{identifier}"

# The stored clip: constant frame rate (the renderer reads it frame by
# frame in sync with the film), bounded size, even dimensions (yuv420p).
CLIP_FPS = 25
MAX_W, MAX_H = 1920, 1080
CLIP_CRF = 20
CLIP_PRESET = "veryfast"
# Where the window starts in a long source: past the leader/titles.
START_FRACTION = 0.10
# Derivative/file choice: the one closest to this height (smaller downloads).
PREFERRED_HEIGHT = 720
# Footage is expensive to fetch: only material some render profile may
# show is downloaded (unknown / permission_required are skipped).
REQUIRE_RENDERABLE_RIGHTS = True
# ... so the Internet Archive is asked only for items that state a license
# (otherwise the few candidate rows fill up with unlicensed mirrors).
IA_REQUIRE_LICENSE = True
# Keyframe dHash distance at which two clips count as the same shot.
DEDUPE_HAMMING = 6
FFMPEG_TIMEOUT_S = 300

_VIDEO_EXT = (".mp4", ".m4v", ".ogv", ".webm", ".mov", ".mpeg", ".mpg")
_BINARY_TYPES = {"application/ogg", "application/octet-stream", "binary/octet-stream",
                 "application/mp4", "application/x-matroska"}


class FootageError(RuntimeError):
    """A candidate failed technically (network, ffmpeg)."""


class FootageRejected(FootageError):
    """A candidate does not meet the limits (size, type, length)."""


@dataclass
class FootageCandidate:
    provider: str
    url: str | None                       # the media file to download
    page_url: str | None = None
    source_url: str | None = None         # canonical identity (dedupe)
    identifier: str | None = None         # Internet Archive item
    title: str | None = None
    caption: str | None = None
    license: str | None = None
    credit: str | None = None
    source_name: str | None = None
    found_for: str | None = None
    date: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    size: int | None = None
    entities: list[str] = field(default_factory=list)
    entity_key: str | None = None
    entity_type: str | None = None
    query_kind: str | None = None


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_length(v) -> float | None:
    """'1166.13' | '00:19:26' | '19:26' | 1166 -> seconds."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    parts = str(v).strip().split(":")
    try:
        secs = 0.0
        for p in parts:
            secs = secs * 60 + float(p)
        return secs
    except ValueError:
        return None


def license_from_url(url: str | None) -> str | None:
    """A license name rights.classify understands, from a license URL
    (Internet Archive's licenseurl)."""
    u = (url or "").strip()
    if not u:
        return None
    low = u.lower()
    if "publicdomain/zero" in low:
        return "CC0 1.0"
    if "publicdomain/mark" in low:
        return "Public Domain Mark 1.0"
    if "publicdomain" in low:
        return "Public domain"
    m = re.search(r"creativecommons\.org/licenses/([a-z\-]+)/?([\d.]+)?", low)
    if m:
        return f"CC {m.group(1).upper()} {m.group(2) or ''}".strip()
    return u[:200]


def renderable(rights_status: str) -> bool:
    """Some render profile may show it (preview at least)."""
    return any(rights_status in allowed
               for allowed in ai_config.rights.allowed_for_render.values())


def clip_window(duration: float, clip_seconds: float,
                start_fraction: float = START_FRACTION) -> tuple[float, float]:
    """(start, length) of the stored window: a short file whole; a longer
    one from 10 % in (past leaders and titles), clamped to fit."""
    if not duration or duration <= 0:
        return 0.0, 0.0
    if duration <= clip_seconds:
        return 0.0, round(duration, 3)
    start = min(duration * start_fraction, duration - clip_seconds)
    return round(max(start, 0.0), 3), round(clip_seconds, 3)


def footage_queries(queries: list[dict], max_queries: int) -> list[dict]:
    """Which searches also look for footage: those flagged footage=True;
    when none is flagged, the first query of each entity in the given
    (priority) order — exact queries before context ones."""
    if max_queries <= 0:
        return []
    flagged = [q for q in queries if q.get("footage")]
    if flagged:
        return flagged[:max_queries]
    ordered = ([q for q in queries if q.get("kind") != "context"]
               + [q for q in queries if q.get("kind") == "context"])
    out, seen = [], set()
    for q in ordered:
        key = q.get("entity") or q.get("query")
        if not q.get("query") or key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= max_queries:
            break
    return out


def pick_derivative(info: dict, min_height: int,
                    max_bytes: int) -> tuple[str, int | None, int | None, int | None] | None:
    """Commons: (url, width, height, size) of the file to download — a
    transcode near PREFERRED_HEIGHT (WebM first), else the original when
    it is small enough."""
    ok = []
    for d in info.get("derivatives") or []:
        h = _int(d.get("height")) or 0
        if not d.get("src") or not d.get("transcodekey") or not min_height <= h <= MAX_H:
            continue
        webm = 0 if str(d.get("src")).lower().endswith(".webm") else 1
        ok.append(((abs(h - PREFERRED_HEIGHT), webm, h, str(d["transcodekey"])), d))
    if ok:
        d = min(ok, key=lambda x: x[0])[1]
        return d["src"], _int(d.get("width")), _int(d.get("height")), None
    h = _int(info.get("height")) or 0
    size = _int(info.get("size")) or 0
    if info.get("url") and h >= min_height and size <= max_bytes:
        return info["url"], _int(info.get("width")), h, size or None
    return None


def pick_ia_file(files: list[dict], min_height: int, max_bytes: int,
                 max_seconds: float) -> dict | None:
    """Internet Archive: the item file to download — MP4 first, then
    WebM/OGV, height near PREFERRED_HEIGHT, smallest; never above the
    size cap, below min_height or longer than max_seconds."""
    best = []
    for f in files or []:
        name = str(f.get("name") or "")
        low = name.lower()
        if not low.endswith(_VIDEO_EXT):
            continue
        size, h = _int(f.get("size")), _int(f.get("height"))
        length = parse_length(f.get("length"))
        if ((size and size > max_bytes) or (h and h < min_height)
                or (length and length > max_seconds)):
            continue
        pref = 0 if low.endswith((".mp4", ".m4v")) else 1 if low.endswith((".webm", ".ogv")) else 2
        best.append(((pref, abs((h or PREFERRED_HEIGHT) - PREFERRED_HEIGHT), size or 0, name), f))
    return min(best, key=lambda x: x[0])[1] if best else None


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

_last_request: dict[str, float] = {}


async def _polite_wait(url: str) -> None:
    """Space requests per host (public archives' robot policies)."""
    host = urlparse(url).hostname or ""
    gap = ai_config.visual_search.min_request_interval_s
    wait = gap - (time.monotonic() - _last_request.get(host, -1e9))
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request[host] = time.monotonic()


async def _get_json(client: httpx.AsyncClient, url: str, **kw):
    await _polite_wait(url)
    r = await client.get(url, **kw)
    r.raise_for_status()
    return r.json()


class WikimediaVideoProvider:
    name = "wikimedia_video"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def search(self, query: str, limit: int) -> list[FootageCandidate]:
        cfg = ai_config.footage
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{query} filetype:video", "gsrnamespace": 6,
            "gsrlimit": limit, "prop": "videoinfo",
            "viprop": "url|extmetadata|size|mime|derivatives",
        }
        try:
            data = await _get_json(self.client, ai_config.visual_search.wikimedia_api,
                                   params=params)
            pages = (data.get("query") or {}).get("pages") or {}
        except (httpx.HTTPError, ValueError) as e:
            log.warning("wikimedia video search failed for %r: %s", query, e)
            return []
        out = []
        max_bytes = int(cfg.max_download_mb * 1024 * 1024)
        for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
            info = (page.get("videoinfo") or page.get("imageinfo") or [{}])[0]
            mime = str(info.get("mime") or "")
            if not (mime.startswith("video/") or mime == "application/ogg"):
                continue
            pick = pick_derivative(info, cfg.min_height, max_bytes)
            if pick is None:
                continue
            url, w, h, size = pick
            meta = info.get("extmetadata") or {}

            def mv(key, meta=meta):
                return (meta.get(key) or {}).get("value")

            artist = _clean(mv("Artist"), 200)
            out.append(FootageCandidate(
                provider=self.name, url=url, page_url=info.get("descriptionurl"),
                source_url=str(info.get("url") or url).split("?")[0],
                title=(page.get("title") or "").removeprefix("File:"),
                caption=_clean(mv("ImageDescription")),
                license=_clean(mv("LicenseShortName"), 120),
                credit=f"{artist or 'Unknown author'} / Wikimedia Commons",
                source_name="Wikimedia Commons", found_for=query,
                date=_clean(mv("DateTimeOriginal"), 20),
                duration=parse_length(info.get("duration")), width=w, height=h, size=size,
            ))
        return out

    async def resolve(self, c: FootageCandidate) -> FootageCandidate | None:
        return c  # the search already chose the file


class InternetArchiveProvider:
    name = "internet_archive"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def search(self, query: str, limit: int) -> list[FootageCandidate]:
        q = f"({query}) AND mediatype:movies"
        if IA_REQUIRE_LICENSE and REQUIRE_RENDERABLE_RIGHTS:
            q += " AND licenseurl:*"
        params = {
            "q": q,
            "fl[]": ["identifier", "title", "licenseurl", "date", "creator"],
            "rows": limit, "page": 1, "output": "json",
        }
        try:
            data = await _get_json(self.client, ai_config.footage.internet_archive_api,
                                   params=params)
            docs = (data.get("response") or {}).get("docs") or []
        except (httpx.HTTPError, ValueError) as e:
            log.warning("internet archive search failed for %r: %s", query, e)
            return []
        out = []
        for d in docs[:limit]:
            ident = str(_first(d.get("identifier")) or "").strip()
            if not ident:
                continue
            creator = d.get("creator")
            creator = ", ".join(map(str, creator)) if isinstance(creator, list) else creator
            page = IA_DETAILS_URL.format(identifier=quote(ident))
            out.append(FootageCandidate(
                provider=self.name, url=None, page_url=page, source_url=page, identifier=ident,
                title=_clean(_first(d.get("title")), 300),
                license=license_from_url(_first(d.get("licenseurl"))),
                credit=f"{_clean(creator, 200) or 'Unknown author'} / Internet Archive",
                source_name="Internet Archive", found_for=query,
                date=(str(_first(d.get("date")) or "")[:10] or None),
            ))
        return out

    async def resolve(self, c: FootageCandidate) -> FootageCandidate | None:
        """Pick one downloadable file of the item (metadata API)."""
        cfg = ai_config.footage
        try:
            data = await _get_json(
                self.client, IA_METADATA_URL.format(identifier=quote(c.identifier or "")))
        except (httpx.HTTPError, ValueError) as e:
            log.info("internet archive metadata failed for %s: %s", c.identifier, e)
            return None
        meta = data.get("metadata") or {}
        if not c.license and meta.get("licenseurl"):
            c.license = license_from_url(_first(meta.get("licenseurl")))
        f = pick_ia_file(data.get("files") or [], cfg.min_height,
                         int(cfg.max_download_mb * 1024 * 1024), cfg.max_source_seconds)
        if f is None:
            return None
        c.url = IA_DOWNLOAD_URL.format(identifier=quote(c.identifier or ""), name=quote(f["name"]))
        c.duration = parse_length(f.get("length"))
        c.width, c.height, c.size = _int(f.get("width")), _int(f.get("height")), _int(f.get("size"))
        c.caption = c.caption or _clean(_first(meta.get("description")))
        return c


PROVIDERS = {"wikimedia_video": WikimediaVideoProvider, "internet_archive": InternetArchiveProvider}


# ---------------------------------------------------------------------------
# files: download, probe, clip, keyframe
# ---------------------------------------------------------------------------


async def download_video(url: str, client: httpx.AsyncClient, path: Path, max_bytes: int) -> int:
    """Stream a media file to `path`; refuses non-video responses and
    anything above max_bytes (declared or actual)."""
    await _polite_wait(url)
    n = 0
    try:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype and not (ctype.startswith("video/") or ctype in _BINARY_TYPES):
                raise FootageRejected(f"not a video ({ctype})")
            if (_int(r.headers.get("content-length")) or 0) > max_bytes:
                raise FootageRejected("video too large")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as fh:
                async for chunk in r.aiter_bytes():
                    n += len(chunk)
                    if n > max_bytes:
                        raise FootageRejected("video too large")
                    fh.write(chunk)
    except httpx.HTTPError as e:
        raise FootageError(f"download failed: {type(e).__name__}") from e
    return n


def probe_video(path: Path) -> dict | None:
    """{"duration", "width", "height", "has_video", "has_audio"} or None."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height,duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout or "{}")
    except ValueError:
        return None
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    dur = parse_length((data.get("format") or {}).get("duration")) or \
        parse_length((v or {}).get("duration"))
    return {"duration": dur, "width": _int((v or {}).get("width")),
            "height": _int((v or {}).get("height")), "has_video": v is not None,
            "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def make_clip(src: Path, out: Path, start: float, length: float,
              max_w: int = MAX_W, max_h: int = MAX_H, crf: int = CLIP_CRF) -> None:
    """The stored clip: window [start, start+length] of the source, no
    audio stream (-an), square pixels, at most max_w x max_h, even
    dimensions, constant CLIP_FPS, H.264 yuv420p."""
    vf = ("scale='trunc(iw*sar/2)*2':'trunc(ih/2)*2',setsar=1,"
          f"scale='min({max_w},iw)':'min({max_h},ih)':force_original_aspect_ratio=decrease,"
          "scale='trunc(iw/2)*2':'trunc(ih/2)*2',"
          f"fps={CLIP_FPS}")
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src),
           "-t", f"{length:.3f}", "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", vf,
           "-c:v", "libx264", "-preset", CLIP_PRESET, "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-r", str(CLIP_FPS), "-map_metadata", "-1",
           "-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S,
                         check=False)
    if res.returncode != 0 or not out.exists():
        raise FootageError(f"ffmpeg clip failed: {res.stderr[-300:]}")


def extract_keyframe(clip: Path, at: float, out: Path) -> Path:
    """One frame of the clip as JPEG (the clip's thumbnail and the image
    the vision verifier judges)."""
    for t in (at, 0.0):
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(t, 0.0):.3f}", "-i", str(clip),
               "-frames:v", "1", "-q:v", "2", str(out)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        if res.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
    raise FootageError("no keyframe could be extracted")


SHEET_POINTS = (0.12, 0.5, 0.88)


def sheet_path(case_id: int, code: str) -> Path:
    return storage.thumbs_dir(case_id) / f"{code}.sheet.jpg"


def frame_sheet(clip: Path, start: float, end: float, out: Path) -> Path:
    """Three frames of the clip — near its start, middle and end — side by
    side in one JPEG: what the verifier and the auditor judge a clip by
    (one frame cannot tell what a clip shows a few seconds later)."""
    from PIL import Image

    length = max(end - start, 0.0)
    frames = []
    try:
        for k, f in enumerate(SHEET_POINTS):
            tmp = out.with_name(f"{out.stem}.{k}.jpg")
            extract_keyframe(clip, start + length * f, tmp)
            img = IM.open_image(tmp.read_bytes()).convert("RGB")
            tmp.unlink(missing_ok=True)
            h = 360
            frames.append(img.resize((max(1, int(img.width * h / img.height)), h)))
    finally:
        for k in range(len(SHEET_POINTS)):
            out.with_name(f"{out.stem}.{k}.jpg").unlink(missing_ok=True)
    gap = 8
    sheet = Image.new("RGB", (sum(f.width for f in frames) + gap * (len(frames) - 1),
                              frames[0].height), (0, 0, 0))
    x = 0
    for f in frames:
        sheet.paste(f, (x, 0))
        x += f.width + gap
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=85)
    return out


def clip_sheet(asset: VisualAsset) -> Path | None:
    """The asset's three-frame sheet (made once, then reused)."""
    if asset.asset_type != "video":
        return None
    out = sheet_path(asset.case_id, asset.asset_code)
    if out.exists():
        return out
    clip = storage.resolve(asset.local_path)
    if clip is None or not clip.exists():
        return None
    start = float(asset.clip_start or 0.0)
    end = float(asset.clip_end if asset.clip_end is not None
                else (asset.duration_seconds or start))
    return frame_sheet(clip, start, end, out)


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".ogv", ".mpg", ".mpeg"}


def is_video_upload(filename: str | None, content_type: str | None) -> bool:
    ctype = (content_type or "").split(";")[0].strip().lower()
    return ctype.startswith("video/") or Path(filename or "").suffix.lower() in VIDEO_SUFFIXES


async def add_uploaded_video(db: Session, case: Case, src: Path, filename: str,
                       title: str | None = None, caption: str | None = None,
                       asset_role: str = "evidence", rights_status: str = "owned",
                       start: float = 0.0) -> VisualAsset:
    """A video supplied by the production (rights asserted by the
    uploader): stored like found footage — muted H.264, at most
    footage.upload_max_seconds from `start` — with a keyframe and a
    start/middle/end sheet. It starts unverified: the verifier and the
    placement auditor check it like any other clip."""
    cfg = ai_config.footage
    if cfg.pieces:
        from app.documentary.visuals.pieces import ingest_video

        role = asset_role if asset_role in ("evidence", "context", "illustration") else "evidence"
        source, _ = await ingest_video(db, case, src, {
            "provider": "upload", "rights_status": rights_status if rights_status in R.RIGHTS
            else "owned", "rights_reason": "uploaded by the production", "asset_role": role,
            "title": title or filename, "caption": caption, "found_during": "upload",
            "uploaded_name": filename}, start=start, max_seconds=cfg.upload_max_seconds)
        return source
    info = probe_video(src)
    if info is None or not info["has_video"]:
        raise FootageRejected("not a readable video")
    duration = info["duration"] or 0.0
    if duration <= 0.5:
        raise FootageRejected("the video has no length")
    start = max(0.0, min(float(start or 0.0), max(duration - 1.0, 0.0)))
    length = min(duration - start, cfg.upload_max_seconds)
    code = next_asset_code(db)
    vdir = storage.visuals_dir(case.id, "video")
    out, key = vdir / f"{code}.mp4", vdir / f".{code}.key.jpg"
    keep = False
    try:
        make_clip(src, out, start, length)
        clip = probe_video(out)
        if clip is None or not clip["has_video"] or clip["has_audio"]:
            raise FootageError("ffmpeg clip unusable")
        clip_len = round(clip["duration"] or length, 3)
        extract_keyframe(out, clip_len / 2, key)
        img = IM.open_image(key.read_bytes())
        thumb = IM.save_thumbnail(img, storage.thumbs_dir(case.id) / f"{code}.jpg")
        frame_sheet(out, 0.0, clip_len, sheet_path(case.id, code))
        role = asset_role if asset_role in ("evidence", "context", "illustration") else "evidence"
        tier = T.provisional_tier("upload", role)
        asset = VisualAsset(
            case_id=case.id, asset_code=code, asset_type="video",
            title=(title or filename)[:500], caption=caption, provider="upload",
            rights_status=rights_status if rights_status in R.RIGHTS else "owned",
            rights_reason="uploaded by the production", asset_role=role,
            local_path=storage.rel(out), thumbnail_path=storage.rel(thumb),
            width=clip["width"], height=clip["height"], phash=IM.dhash(img),
            sha256=IM.sha256_file(out), duration_seconds=clip_len,
            has_original_audio=False, clip_start=0.0, clip_end=clip_len,
            verification_status="unverified", relevance_tier=tier,
            case_relevance=T.tier_label(tier), found_during="upload",
            spec_json=json.dumps({"uploaded_name": filename,
                                  "source_window": [round(start, 3), round(start + length, 3)],
                                  "source_duration": round(duration, 3),
                                  "source_had_audio": info["has_audio"]}),
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        keep = True
        return asset
    finally:
        key.unlink(missing_ok=True)
        if not keep:
            out.unlink(missing_ok=True)
            sheet_path(case.id, code).unlink(missing_ok=True)


def _meta_of(c: "FootageCandidate", status: str, reason: str, found_during: str) -> dict:
    tier, why = T.provisional_why(c.provider, "context", c.entity_type, c.query_kind)
    return {
        "provider": c.provider, "source_url": c.source_url or c.url, "page_url": c.page_url,
        "source_name": (c.source_name or "")[:300] or None,
        "found_for": (c.found_for or "")[:300] or None,
        "license": (c.license or "")[:200] or None, "credit": (c.credit or "")[:500] or None,
        "rights_status": status, "rights_reason": reason, "asset_role": "context",
        "entity_key": c.entity_key, "entity_type": c.entity_type, "entities": c.entities,
        "title": c.title, "caption": c.caption, "found_during": found_during,
        "date_start": (c.date or "")[:20] or None, "relevance_tier": tier,
        "query_kind": c.query_kind, "media_url": c.url, "tier_reason": why,
    }


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------


class FootageAgent:
    """Searches the footage providers for the given queries and adds
    muted clips to the case library (bounded by footage.max_clips_per_case)."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.cfg = ai_config.footage

    def providers(self) -> list:
        return [PROVIDERS[p](self.client) for p in self.cfg.providers if p in PROVIDERS]

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
        providers = self.providers()
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

    async def _ingest_pieces(self, db, case, c: FootageCandidate, status: str, reason: str,
                             hashes: list[str], found_during: str) -> str:
        """The video kept whole (muted) and cut into described pieces."""
        from app.documentary.visuals.pieces import ingest_video

        code = next_asset_code(db)
        tmp = storage.visuals_dir(case.id, "video") / f".{code}.download"
        try:
            await download_video(c.url, self.client, tmp,
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
            source, pieces = await ingest_video(
                db, case, tmp, _meta_of(c, status, reason, found_during), start)
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
            await download_video(c.url, self.client, tmp, int(cfg.max_download_mb * 1024 * 1024))
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
