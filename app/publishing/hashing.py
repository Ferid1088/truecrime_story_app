"""The hashes of the publishing layer. A file's sha256 is streamed (never read whole) and kept by path, size and modification time so that opening a page does not read a
film again; a live operation always uses `fresh_file_sha256`. The publication hash binds an approval to everything that is approved."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.files import file_sha256
from app.db.models import FileHashCache


def fresh_file_sha256(path: Path) -> str:
    """Always reads the file (streamed). Used before any live upload."""
    return file_sha256(path)


def cached_file_sha256(db: Session, path: Path) -> str:
    """The file's sha256, read again only when its size or modification time changed since it was last hashed."""
    stat = path.stat()
    key = str(path.resolve())
    row = db.get(FileHashCache, key)
    if row is not None and row.size_bytes == stat.st_size and row.mtime_ns == stat.st_mtime_ns:
        return row.sha256
    digest = file_sha256(path)
    if row is None:
        db.add(FileHashCache(path=key, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=digest))
    else:
        row.size_bytes, row.mtime_ns, row.sha256 = stat.st_size, stat.st_mtime_ns, digest
    db.commit()
    return digest


def composite_hash(inputs: dict) -> str:
    """The sha256 of the inputs of an approval (file hash, cover hash, caption, links, settings), in a fixed order."""
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
