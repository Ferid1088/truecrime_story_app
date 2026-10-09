"""Video pieces: a found or uploaded video is kept whole (muted proxy) and
cut at its scene changes into short pieces, each its own library asset
with a name and a description — so a sentence shows exactly the part of a
video that belongs to it, and other parts can serve other sentences.

  source (asset_type "video_source")  the whole muted proxy, never on screen
  pieces (asset_type "video")         windows [clip_start, clip_end] of the
                                      proxy; spec_json {"parent", "piece",
                                      "window"}; described and checked by
                                      the video auditor

Cutting: ffmpeg's scene-change score finds the cuts; scenes shorter than
footage.piece_min_seconds join a neighbour, a scene longer than
piece_max_seconds is split into pieces that overlap by
piece_overlap_seconds (so each piece starts and ends somewhere
meaningful). Two pieces that overlap are never both shown in one film
(usage tracker).
"""

from __future__ import annotations

import json
import re
import subprocess
from itertools import pairwise
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


def plan_pieces(duration: float, cuts: list[float], min_s: float | None = None,
                max_s: float | None = None, overlap: float | None = None,
                max_pieces: int | None = None) -> list[tuple[float, float]]:
    """[(start, end)] of the pieces of a video of `duration` seconds."""
    cfg = ai_config.footage
    min_s = cfg.piece_min_seconds if min_s is None else min_s
    max_s = cfg.piece_max_seconds if max_s is None else max_s
    overlap = cfg.piece_overlap_seconds if overlap is None else overlap
    max_pieces = cfg.max_pieces_per_source if max_pieces is None else max_pieces
    if duration <= 0:
        return []
    bounds = [0.0] + [c for c in cuts if 0.0 < c < duration] + [duration]
    merged = [[a, b] for a, b in pairwise(bounds) if b - a > 0.05]
    # a too-short scene joins its shorter neighbour (never a flash piece)
    while len(merged) > 1:
        short = [k for k, (a, b) in enumerate(merged) if b - a < min_s]
        if not short:
            break
        k = short[0]
        if k == 0:
            j = 1
        elif k == len(merged) - 1:
            j = k - 1
        else:
            before = merged[k - 1][1] - merged[k - 1][0]
            after = merged[k + 1][1] - merged[k + 1][0]
            j = k - 1 if before <= after else k + 1
        lo, hi = sorted((k, j))
        merged[lo:hi + 1] = [[merged[lo][0], merged[hi][1]]]
    pieces: list[tuple[float, float]] = []
    for a, b in merged:
        length = b - a
        if length <= max_s:
            pieces.append((round(a, 3), round(b, 3)))
            continue
        # a long scene: pieces of ~max_s that overlap a little
        step = max(max_s - overlap, min_s)
        t = a
        while t < b - 0.05:
            end = min(t + max_s, b)
            if b - end < min_s:  # the rest would be a stub: this piece takes it
                end = b
            pieces.append((round(t, 3), round(end, 3)))
            if end >= b:
                break
            t += step
    if len(pieces) > max_pieces:  # spread over the whole video, not its start
        k = len(pieces) / max_pieces
        pieces = [pieces[int(i * k)] for i in range(max_pieces)]
    return pieces


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


def ingest_video(db: Session, case: Case, src: Path, meta: dict,
                 start: float = 0.0, max_seconds: float | None = None
                 ) -> tuple[VisualAsset, list[VisualAsset]]:
    """Keep the video whole (muted proxy) and cut it into pieces.

    meta: VisualAsset fields shared by the source and its pieces (provider,
    source_url, page_url, source_name, found_for, license, credit,
    rights_status, rights_reason, asset_role, entity_key, entity_type,
    entities (list), title, caption, found_during, date_start) plus
    "query_kind" and "uploaded_name" for the record. Pieces start
    unverified: the video auditor describes and checks each one."""
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
        common = dict(  # noqa: C408 — keyword list shared by source and pieces
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
            has_original_audio=False, local_path=storage.rel(proxy))
        source = VisualAsset(
            asset_code=code, asset_type=SOURCE_TYPE, title=(meta.get("title") or code)[:500],
            thumbnail_path=storage.rel(thumb), phash=IM.dhash(img),
            sha256=IM.sha256_file(proxy), duration_seconds=length, clip_start=0.0,
            clip_end=length, verification_status="source",
            spec_json=json.dumps({
                "kind": "source", "source_window": [round(start, 3), round(start + keep, 3)],
                "source_duration": round(duration, 3), "source_had_audio": info["has_audio"],
                "query_kind": meta.get("query_kind"), "uploaded_name": meta.get("uploaded_name"),
                "media_url": meta.get("media_url")}), **common)
        db.add(source)
        db.flush()
        pieces: list[VisualAsset] = []
        for k, (a, b) in enumerate(plan_pieces(length, detect_cuts(proxy)), 1):
            pcode = next_asset_code(db)
            pkey = vdir / f".{pcode}.key.jpg"
            extract_keyframe(proxy, (a + b) / 2, pkey)
            pimg = IM.open_image(pkey.read_bytes())
            pkey.unlink(missing_ok=True)
            pthumb = IM.save_thumbnail(pimg, storage.thumbs_dir(case.id) / f"{pcode}.jpg")
            made.append(pthumb)
            piece = VisualAsset(
                asset_code=pcode, asset_type="video",
                title=f"{(meta.get('title') or code)[:440]} — piece {k}",
                thumbnail_path=storage.rel(pthumb), phash=IM.dhash(pimg),
                sha256=source.sha256, duration_seconds=round(b - a, 3),
                clip_start=a, clip_end=b, verification_status="unverified",
                spec_json=json.dumps({"parent": code, "piece": k, "window": [a, b]}),
                **common)
            db.add(piece)
            db.flush()
            frame_sheet(proxy, a, b, sheet_path(case.id, pcode))
            made.append(sheet_path(case.id, pcode))
            pieces.append(piece)
        db.commit()
        return source, pieces
    except BaseException:
        db.rollback()
        for p in made:
            Path(p).unlink(missing_ok=True)
        raise


def remove_video(db: Session, source: VisualAsset) -> None:
    """Drop a source and its pieces (and their files) — a duplicate."""
    rows = [r for r in db.query(VisualAsset).filter(
        VisualAsset.case_id == source.case_id, VisualAsset.asset_type == "video").all()
            if piece_info(r).get("parent") == source.asset_code]
    files = [storage.resolve(source.local_path), storage.resolve(source.thumbnail_path)]
    for r in rows:
        files += [storage.resolve(r.thumbnail_path), sheet_path(r.case_id, r.asset_code)]
        db.delete(r)
    db.delete(source)
    db.commit()
    for f in files:
        if f is not None:
            Path(f).unlink(missing_ok=True)
