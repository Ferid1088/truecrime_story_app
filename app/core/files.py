"""Serve stored files only from the project's own data folders.

File paths come from database rows. A wrong or planted row (`/etc/passwd`, `../../.env`) must never
turn a download endpoint into a way to read arbitrary files, so every path is resolved
(symlinks and `..` included) and must sit under one of the allowed roots.
"""
from __future__ import annotations

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


def serve(path: Path | None, media_type: str, filename: str | None = None, missing: str = "File missing"):
    """FileResponse for a stored file, or 404. A path outside the data folders looks the same as a missing file."""
    if path is None or not is_allowed(path) or not path.is_file():
        raise HTTPException(status_code=404, detail=missing)
    return FileResponse(path, media_type=media_type, filename=filename)
