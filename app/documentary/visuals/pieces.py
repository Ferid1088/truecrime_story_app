"""Video pieces: a found or uploaded video is kept whole (muted proxy) and
cut at its scene changes into short pieces, each its own library asset
with a name and a description — so a sentence shows exactly the part of a
video that belongs to it, and other parts can serve other sentences.

  source (asset_type "video_source")  the whole muted proxy, never on screen
  pieces (asset_type "video")         windows [clip_start, clip_end] of the
                                      proxy; spec_json {"parent", "piece",
                                      "window"}; described and checked by
                                      the video auditor

Cutting is by MEANING (video_segmenter.py): the segmenter watches the
video (frames in order + the scene changes ffmpeg finds) and proposes
complete, meaningful moments with a name and a description; the video
auditor then checks each piece's cut, name, description and content
(video_auditor.py). Never by the clock. Two pieces that overlap are never
both shown in one film (usage tracker).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import tiers as T
from app.documentary.visuals.footage import (
    CLIP_CRF,
    FootageError,
    FootageRejected,
    extract_keyframe,
    frame_sheet,
    make_clip,
    probe_video,
    sheet_path,
)

SOURCE_TYPE = "video_source"
_PTS = re.compile(r"pts_time:([0-9.]+)")


def detect_cuts(path: Path, threshold: float | None = None) -> list[float]:
    """Seconds where the picture changes scene (ffmpeg scene score)."""
    thr = ai_config.footage.scene_threshold if threshold is None else threshold
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
             f"select='gt(scene,{thr})',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return sorted({round(float(m), 3) for m in _PTS.findall(res.stderr or "")})


def piece_frames(asset: VisualAsset, fps: float | None = None, max_frames: int | None = None,
                 width: int | None = None) -> list[bytes]:
    """JPEG frames of a piece in order (frames_per_second, at most
    max_frames, evenly spread when the piece is longer)."""
    cfg = ai_config.video_audit
    fps = cfg.frames_per_second if fps is None else fps
    max_frames = cfg.max_frames if max_frames is None else max_frames
    width = cfg.frame_width if width is None else width
    clip = storage.resolve(asset.local_path)
    if clip is None or not clip.exists():
        raise FootageError("video file missing")
    start = float(asset.clip_start or 0.0)
    end = float(asset.clip_end if asset.clip_end is not None
                else (asset.duration_seconds or start + 1.0))
    length = max(end - start, 0.1)
    n = max(3, min(max_frames, int(length * fps) or 1))
    times = [start + length * (i + 0.5) / n for i in range(n)]
    out: list[bytes] = []
    for t in times:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(clip), "-frames:v", "1",
             "-vf", f"scale={width}:-2", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg",
             "-"], capture_output=True, timeout=60, check=False)
        if res.returncode == 0 and res.stdout:
            out.append(res.stdout)
    if not out:
        raise FootageError("no frames could be read")
    return out


def is_source(a: VisualAsset | None) -> bool:
    return a is not None and a.asset_type == SOURCE_TYPE


def piece_info(a: VisualAsset) -> dict:
    """{"parent", "piece", "window"} of a piece ({} for anything else)."""
    if a.asset_type != "video":
        return {}
    spec = json.loads(a.spec_json or "{}") if a.spec_json else {}
    if not spec.get("parent"):
        return {}
    return {"parent": spec["parent"], "piece": spec.get("piece"),
            "window": spec.get("window") or [a.clip_start, a.clip_end]}


def siblings(db: Session, a: VisualAsset) -> list[VisualAsset]:
    """The other pieces of the same video."""
    info = piece_info(a)
    if not info:
        return []
    rows = db.query(VisualAsset).filter(VisualAsset.case_id == a.case_id,
                                        VisualAsset.asset_type == "video").all()
    return [r for r in rows if r.id != a.id and piece_info(r).get("parent") == info["parent"]]


def pieces_of(db: Session, source: VisualAsset) -> list[VisualAsset]:
    """The pieces cut from a kept video, in their order."""
    rows = db.query(VisualAsset).filter(VisualAsset.case_id == source.case_id,
                                        VisualAsset.asset_type == "video").all()
    return sorted((r for r in rows if piece_info(r).get("parent") == source.asset_code),
                  key=lambda r: (r.clip_start or 0.0, r.id))


_SHARED = ("case_id", "provider", "source_url", "page_url", "source_name", "found_for",
           "license", "credit", "rights_status", "rights_reason", "asset_role", "entity_key",
           "entity_type", "entities_json", "caption", "found_during", "date_start",
           "relevance_tier", "case_relevance", "width", "height", "has_original_audio",
           "local_path", "sha256")


def keep_video(db: Session, case: Case, src: Path, meta: dict, start: float = 0.0,
               max_seconds: float | None = None) -> tuple[VisualAsset, list[float]]:
    """The video kept whole: a muted proxy (at most max_keep_seconds from
    `start`), its thumbnail and the source row (never on screen), plus the
    scene changes ffmpeg finds in it. Committed: the video is kept even if
    cutting it fails later.

    meta: VisualAsset fields shared by the source and its pieces (provider,
    source_url, page_url, source_name, found_for, license, credit,
    rights_status, rights_reason, asset_role, entity_key, entity_type,
    entities (list), title, caption, found_during, date_start) plus
    "query_kind", "uploaded_name" and "media_url" for the record."""
    from app.documentary.visuals.research import next_asset_code

    cfg = ai_config.footage
    info = probe_video(src)
    if info is None or not info["has_video"]:
        raise FootageRejected("not a readable video")
    duration = info["duration"] or 0.0
    if duration <= 0.5:
        raise FootageRejected("the video has no length")
    start = max(0.0, min(float(start or 0.0), max(duration - 1.0, 0.0)))
    keep = min(duration - start, max_seconds or cfg.max_keep_seconds)
    code = next_asset_code(db)
    vdir = storage.visuals_dir(case.id, "video")
    proxy = vdir / f"{code}.mp4"
    made: list[Path] = [proxy]
    try:
        h = cfg.proxy_height
        make_clip(src, proxy, start, keep, max_w=int(h * 16 / 9) // 2 * 2, max_h=h,
                  crf=CLIP_CRF + 3)
        clip = probe_video(proxy)
        if clip is None or not clip["has_video"] or clip["has_audio"]:
            raise FootageError("ffmpeg proxy unusable")
        length = round(clip["duration"] or keep, 3)
        key = vdir / f".{code}.key.jpg"
        extract_keyframe(proxy, length / 2, key)
        img = IM.open_image(key.read_bytes())
        key.unlink(missing_ok=True)
        thumb = IM.save_thumbnail(img, storage.thumbs_dir(case.id) / f"{code}.jpg")
        made.append(thumb)
        role = meta.get("asset_role") or "context"
        tier = meta.get("relevance_tier") or T.provisional_tier(
            meta.get("provider"), role, meta.get("entity_type"))
        cuts = detect_cuts(proxy)
        source = VisualAsset(
            case_id=case.id, provider=meta.get("provider") or "upload",
            source_url=meta.get("source_url"), page_url=meta.get("page_url"),
            source_name=meta.get("source_name"), found_for=meta.get("found_for"),
            license=meta.get("license"), credit=meta.get("credit"),
            rights_status=meta.get("rights_status") or "unknown",
            rights_reason=meta.get("rights_reason"), asset_role=role,
            entity_key=meta.get("entity_key"), entity_type=meta.get("entity_type"),
            entities_json=json.dumps(list(meta.get("entities") or []), ensure_ascii=False),
            caption=meta.get("caption"), found_during=meta.get("found_during") or "research",
            date_start=meta.get("date_start"), relevance_tier=tier,
            case_relevance=T.tier_label(tier), width=clip["width"], height=clip["height"],
            has_original_audio=False, local_path=storage.rel(proxy),
            asset_code=code, asset_type=SOURCE_TYPE, title=(meta.get("title") or code)[:500],
            thumbnail_path=storage.rel(thumb), phash=IM.dhash(img),
            sha256=IM.sha256_file(proxy), duration_seconds=length, clip_start=0.0,
            clip_end=length, verification_status="source",
            spec_json=json.dumps({
                "kind": "source", "source_window": [round(start, 3), round(start + keep, 3)],
                "source_duration": round(duration, 3), "source_had_audio": info["has_audio"],
                "scene_changes": cuts, "query_kind": meta.get("query_kind"),
                "uploaded_name": meta.get("uploaded_name"), "media_url": meta.get("media_url"),
                "tier_reason": meta.get("tier_reason")}))
        db.add(source)
        db.commit()
        return source, cuts
    except BaseException:
        db.rollback()
        for p in made:
            Path(p).unlink(missing_ok=True)
        raise


def store_pieces(db: Session, source: VisualAsset, proposals: list[dict]) -> list[VisualAsset]:
    """One library asset per approved-to-check proposal: a window of the
    source with the segmenter's name and description. Unverified until the
    video auditor has checked the cut, the description and the content."""
    from app.documentary.visuals.research import next_asset_code

    proxy = storage.resolve(source.local_path)
    case_id = source.case_id
    made: list[Path] = []
    pieces: list[VisualAsset] = []
    try:
        for k, p in enumerate(proposals, 1):
            a, b = p["start"], p["end"]
            pcode = next_asset_code(db)
            pkey = proxy.parent / f".{pcode}.key.jpg"
            extract_keyframe(proxy, (a + b) / 2, pkey)
            pimg = IM.open_image(pkey.read_bytes())
            pkey.unlink(missing_ok=True)
            pthumb = IM.save_thumbnail(pimg, storage.thumbs_dir(case_id) / f"{pcode}.jpg")
            made.append(pthumb)
            piece = VisualAsset(
                **{f: getattr(source, f) for f in _SHARED},
                asset_code=pcode, asset_type="video", title=p["name"][:500],
                description=p["description"], thumbnail_path=storage.rel(pthumb),
                phash=IM.dhash(pimg), duration_seconds=round(b - a, 3),
                clip_start=a, clip_end=b, verification_status="unverified",
                spec_json=json.dumps({"parent": source.asset_code, "piece": k, "window": [a, b],
                                      "cut_by": "segmenter",
                                      "why_here": p.get("why_here")}, ensure_ascii=False))
            db.add(piece)
            db.flush()
            frame_sheet(proxy, a, b, sheet_path(case_id, pcode))
            made.append(sheet_path(case_id, pcode))
            pieces.append(piece)
        db.commit()
        return pieces
    except BaseException:
        db.rollback()
        for f in made:
            Path(f).unlink(missing_ok=True)
        raise


async def ingest_video(db: Session, case: Case, src: Path, meta: dict, start: float = 0.0,
                       max_seconds: float | None = None, segmenter=None
                       ) -> tuple[VisualAsset, list[VisualAsset]]:
    """Keep the video whole, then cut it by meaning (the segmenter). When
    the segmenter fails or finds nothing usable, the video is kept without
    pieces (and the reason recorded) — it is never cut by the clock."""
    import asyncio

    source, _ = await asyncio.to_thread(keep_video, db, case, src, meta, start, max_seconds)
    return source, await cut_video(db, case, source, segmenter)


async def cut_video(db: Session, case: Case, source: VisualAsset, segmenter=None
                    ) -> list[VisualAsset]:
    """Cut a kept video by meaning — also again later ("Cut again" in the
    library, or the next research run) when the segmenter failed before.
    Pieces from an earlier cut are kept; only a video WITHOUT pieces is
    cut. On failure the reason is recorded on the source and nothing is
    cut by the clock instead."""
    import asyncio
    import logging

    from app.documentary.visuals.video_segmenter import VideoSegmenter

    done = pieces_of(db, source)
    if done:
        return done
    spec = json.loads(source.spec_json or "{}")
    cuts = list(spec.get("scene_changes") or [])
    try:
        proposals = await (segmenter or VideoSegmenter()).cut(
            db, case, source, storage.resolve(source.local_path),
            float(source.duration_seconds or 0), cuts)
        if not proposals:
            raise FootageRejected("the segmenter found no meaningful piece")
    except Exception as e:  # noqa: BLE001 — kept, recorded, can be cut again later
        logging.getLogger(__name__).warning("cutting %s failed: %s", source.asset_code, e)
        spec["segment_error"] = f"{type(e).__name__}: {e}"[:300]
        spec["segment_attempts"] = int(spec.get("segment_attempts") or 0) + 1
        source.spec_json = json.dumps(spec)
        db.commit()
        return []
    pieces = await asyncio.to_thread(store_pieces, db, source, proposals)
    spec["pieces"] = [p.asset_code for p in pieces]
    spec.pop("segment_error", None)
    source.spec_json = json.dumps(spec)
    db.commit()
    return pieces


def remove_video(db: Session, source: VisualAsset) -> None:
    """Drop a source and its pieces (and their files) — a duplicate."""
    rows = pieces_of(db, source)
    files = [storage.resolve(source.local_path), storage.resolve(source.thumbnail_path)]
    for r in rows:
        files += [storage.resolve(r.thumbnail_path), sheet_path(r.case_id, r.asset_code)]
        db.delete(r)
    db.delete(source)
    db.commit()
    for f in files:
        if f is not None:
            Path(f).unlink(missing_ok=True)
