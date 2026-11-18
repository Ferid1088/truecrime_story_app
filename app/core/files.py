"""Serve stored files only from the project's own data folders.

File paths come from database rows. A wrong or planted row (`/etc/passwd`, `../../.env`) must never
turn a download endpoint into a way to read arbitrary files, so every path is resolved
(symlinks and `..` included) and must sit under one of the allowed roots.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.documentary import storage


def allowed_roots() -> list[Path]:
    from app.core.ai_config import ai_config

    thumbs = Path(ai_config.thumbnail.storage_dir)
    roots = [storage.ROOT / "data", storage.cases_root(),
             thumbs if thumbs.is_absolute() else storage.ROOT / thumbs]
    try:
        from app.documentary.music import MusicLibrary

        roots.append(MusicLibrary().dir)
    except Exception:  # noqa: BLE001  (the music folder is optional)
        pass
    return [r.resolve() for r in roots]


def is_allowed(path: Path) -> bool:
    try:
        real = path.resolve()
    except OSError:
        return False
    return any(real == r or real.is_relative_to(r) for r in allowed_roots())


def serve(path: Path | None, media_type: str, filename: str | None = None, missing: str = "File missing", headers: dict[str, str] | None = None):
    """FileResponse for a stored file, or 404. A path outside the data folders looks the same as a missing file. `headers` are added to the response.
    A stored file can be replaced under the same address (a film cut again), so the browser must ask the server each time (the etag answers 304 when nothing changed) and never replays an old copy."""
    if path is None or not is_allowed(path) or not path.is_file():
        raise HTTPException(status_code=404, detail=missing)
    return FileResponse(path, media_type=media_type, filename=filename, headers={"Cache-Control": "no-cache", **(headers or {})})


def versioned_url(url: str, path: Path | None) -> str:
    """The address of a stored file with a version taken from the file itself (its size and time): a file replaced under the same name gets a new address, so a browser never plays an old copy it kept."""
    if path is None or not path.is_file():
        return url
    stat = path.stat()
    return f"{url}?v={stat.st_size}-{int(stat.st_mtime)}"


CHUNK_BYTES = 1024 * 1024


def file_sha256(path: Path) -> str:
    """The sha256 of a file, read in 1 MB chunks: a film of hundreds of megabytes never sits in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
