"""Content-addressed store for host-led-reel assets that are the same every time they are
needed (outro avatar clips, end cards, cover templates, music library entries): made once per
(asset_type, language, parameters, template_version) and reused by every later reel.

Key = sha256(asset_type, language, canonical(parameters), template_version). A cache hit never
calls ``create``; a miss calls it exactly once and records the resulting file. Every lookup
(hit or miss) bumps the use ledger (``use_count``, ``last_used_at``) so reuse is auditable."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.fingerprint import canonical_json
from app.db.models import SharedReelAsset
from app.utils import utc_now

ROOT = Path(__file__).resolve().parents[2]


def asset_key(asset_type: str, language: str, parameters: dict, template_version: int) -> str:
    payload = canonical_json({
        "asset_type": asset_type, "language": language,
        "parameters": parameters, "template_version": template_version,
    })
    return hashlib.sha256(payload.encode()).hexdigest()


class SharedAssetStore:
    """``get_or_create`` is the only write path: callers never touch
    ``SharedReelAsset`` rows directly, so the ledger can never under-count a reuse."""

    def __init__(self, db: Session, *, root: Path | None = None):
        self.db = db
        self.root = root or ROOT

    def get_or_create(self, asset_type: str, language: str, parameters: dict,
                      template_version: int,
                      create: Callable[[Path], None]) -> tuple[Path, bool]:
        """Return (absolute file path, created). ``create(path)`` is called only on a miss and
        must write a real file at ``path``; the path's parent directory already exists."""
        key = asset_key(asset_type, language, parameters, template_version)
        row = self.db.query(SharedReelAsset).filter_by(asset_key=key).one_or_none()
        now = utc_now()
        if row is not None and row.file_path:
            path = self.root / row.file_path
            if path.exists():
                row.use_count += 1
                row.last_used_at = now
                self.db.commit()
                return path, False
        # Eleven Music returns mp3 by default (no output_format override requested); the stored
        # extension must match the real bytes, not an assumed container.
        ext = {"cover": ".png", "end_card": ".png", "music": ".mp3"}.get(asset_type, ".mp4")
        rel = Path("data/shared_assets") / asset_type / language / f"{key[:24]}{ext}"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        create(path)
        if not path.exists():
            raise RuntimeError(f"shared asset {asset_type}/{language} was not written by its creator")
        if row is None:
            row = SharedReelAsset(
                asset_key=key, asset_type=asset_type, language=language,
                template_version=template_version,
                parameters_json=json.dumps(parameters, sort_keys=True, ensure_ascii=False),
                file_path=str(rel), use_count=1, created_at=now, last_used_at=now,
            )
            self.db.add(row)
        else:
            row.file_path, row.use_count, row.last_used_at = str(rel), row.use_count + 1, now
        self.db.commit()
        return path, True

    def record_use(self, asset_key: str) -> None:
        """Count one more use of a stored asset (the same ledger a lookup bumps)."""
        row = self.db.query(SharedReelAsset).filter_by(asset_key=asset_key).one()
        row.use_count += 1
        row.last_used_at = utc_now()
        self.db.commit()

    def usage(self, asset_type: str | None = None) -> list[dict]:
        q = self.db.query(SharedReelAsset)
        if asset_type:
            q = q.filter_by(asset_type=asset_type)
        return [{"asset_key": r.asset_key, "asset_type": r.asset_type, "language": r.language,
                 "use_count": r.use_count, "file_path": r.file_path,
                 "last_used_at": r.last_used_at} for r in q.order_by(SharedReelAsset.id).all()]
