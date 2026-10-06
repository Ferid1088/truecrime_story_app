"""HTTP fetch cache (Parts 11 + 44).

File-based, content-addressed cache under data/fetch_cache/.
Keyed by canonical URL; stores status, ETag/Last-Modified validators,
content hash, headers and the fetched body (base64). Refresh age and
revalidation are config-driven.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "fetch_cache")


@dataclass
class CachedDocument:
    canonical_url: str
    final_url: str
    status_code: int
    headers: dict
    body: bytes
    etag: str | None
    last_modified: str | None
    content_hash: str
    fetched_at: float

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()


class FetchCache:
    def __init__(self, cache_dir: str | None = None,
                 max_age_s: float = 7 * 24 * 3600):
        self.dir = cache_dir or DEFAULT_DIR
        self.max_age_s = max_age_s
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, canonical_url: str) -> str:
        key = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        return os.path.join(self.dir, f"{key}.json")

    def get(self, canonical_url: str,
            allow_stale: bool = False) -> CachedDocument | None:
        path = self._path(canonical_url)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not allow_stale and time.time() - d["fetched_at"] > self.max_age_s:
                return None
            return CachedDocument(
                canonical_url=d["canonical_url"],
                final_url=d["final_url"],
                status_code=d["status_code"],
                headers=d["headers"],
                body=base64.b64decode(d["body_b64"]),
                etag=d.get("etag"),
                last_modified=d.get("last_modified"),
                content_hash=d["content_hash"],
                fetched_at=d["fetched_at"],
            )
        except (OSError, ValueError, KeyError) as e:
            log.debug("fetch cache read failed for %s: %s", canonical_url, e)
            return None

    def put(self, doc: CachedDocument):
        path = self._path(doc.canonical_url)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "canonical_url": doc.canonical_url,
                    "final_url": doc.final_url,
                    "status_code": doc.status_code,
                    "headers": doc.headers,
                    "body_b64": base64.b64encode(doc.body).decode("ascii"),
                    "etag": doc.etag,
                    "last_modified": doc.last_modified,
                    "content_hash": doc.content_hash,
                    "fetched_at": doc.fetched_at,
                }, f)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("fetch cache write failed: %s", e)


def content_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
