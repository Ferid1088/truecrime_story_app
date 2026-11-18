"""The delivery copy of a finished clip: the same picture and words encoded as the platforms ask (a closed 2 s GOP, a fixed frame rate, AAC stereo at 48 kHz, -14 LUFS, a true peak under -1.5 dB).

It sits beside the original as `delivery_<key>.mp4`; the original is only read and never replaced. Publishing sends the delivery copy when it exists and is not older than the original. Clips
assembled with `clip_video.encode_args` already meet the rules and need no copy; older clips get one here. The settings are in config/parameters/short_clips.json (short_clips.video.delivery)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.core.files import file_sha256
from app.shortform import clip_video as CV


def delivery_path(original: Path) -> Path:
    return original.with_name("delivery_" + original.name.removeprefix("clip_"))


def delivery_of(original: Path | None) -> Path | None:
    """The delivery copy of a clip file, when there is one that is at least as new as the original."""
    if original is None or not original.is_file():
        return None
    copy = delivery_path(original)
    return copy if copy.is_file() and copy.stat().st_mtime >= original.stat().st_mtime else None


def cover_marker(copy: Path) -> Path:
    return copy.with_name(copy.name + ".cover.json")


def cover_of(copy: Path) -> str | None:
    """The sha256 of the cover picture a delivery copy opens with (None: it opens with the clip itself)."""
    try:
        return json.loads(cover_marker(copy).read_text(encoding="utf-8")).get("cover")
    except (OSError, ValueError):
        return None


def ensure_delivery(original: Path, *, force: bool = False, cover: Path | None = None) -> Path:
    """The delivery copy of `original`, made now when it is missing, older than the original, made with another cover than `cover` (or none) or `force`. With a `cover` the copy opens with that
    picture for `delivery.cover_lead_seconds`. Written to a temporary file and moved into place, so a half-made copy is never used."""
    if not original.is_file():
        raise CV.ClipVideoError(f"the clip {original.name} does not exist")
    wanted = file_sha256(cover) if cover is not None else None
    existing = delivery_of(original)
    if existing is not None and not force and cover_of(existing) == wanted:
        return existing
    target = delivery_path(original)
    partial = target.with_name(target.name + ".part.mp4")
    CV.assemble(CV.delivery_command(original, partial, cover=cover))
    os.replace(partial, target)
    cover_marker(target).write_text(json.dumps({"cover": wanted}), encoding="utf-8")
    return target


def prepare_shorts(db, case_id: int, language: str) -> list[dict]:
    """The delivery copy of every Short of a case and language, each opening with its approved reel cover (the same for every language; a Short without an approved cover gets a copy that opens with
    the clip). Nothing is made twice: a copy is rebuilt only when its clip or its cover changed."""
    from app.covers import variants as CV_
    from app.documentary import storage
    from app.publishing.registry import Kind, PublicationRegistry

    done = []
    for asset in PublicationRegistry(db).assets(case_id, language):
        if asset.kind is not Kind.SHORT or asset.path is None or not asset.path.is_file():
            continue
        made = CV_.approved_cover(db, case_id, language, asset.key)
        existing = delivery_of(asset.path)
        stamp = existing.stat().st_mtime_ns if existing is not None else None
        copy = ensure_delivery(asset.path, cover=storage.resolve(made.file_path) if made is not None else None)
        done.append({"key": asset.key, "file": copy.name, "cover": f"reel:{made.id}" if made is not None else None, "made_now": stamp != copy.stat().st_mtime_ns})
    return done
