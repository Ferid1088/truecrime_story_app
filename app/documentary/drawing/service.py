"""The portraits of a case: how many the planner needs, which photos, and making them. A portrait is made once for a photo (the
progress video and the pencil's path, see `portrait.py`) and kept in the case's `drawings` folder; the asset's `spec.drawing` says where
it is and how far the making has come. Making runs in the background (it takes minutes), one worker for each portrait."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import VisualAsset, VisualPlan
from app.documentary import storage

log = logging.getLogger(__name__)
STALE_SECONDS = 180                      # a portrait "being made" that has not reported for this long was interrupted
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="portrait")
_running: set[int] = set()


def folder(case_id: int, asset_code: str) -> Path:
    d = storage.case_dir(case_id) / "drawings" / asset_code
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_of(asset: VisualAsset) -> dict:
    """What is known of this photo's portrait: {status: none|drawing|ready|failed, progress, ...}."""
    spec = json.loads(asset.spec_json or "{}").get("drawing") or {}
    status = spec.get("status", "none")
    if status == "drawing" and (asset.id not in _running) and time.time() - float(spec.get("updated", 0)) > STALE_SECONDS:
        return {**spec, "status": "failed", "error": "the making was interrupted"}
    if status == "ready" and not Path(storage.ROOT / spec.get("video", "")).exists():
        return {"status": "none"}
    return spec if spec else {"status": "none"}


def _save(asset_id: int, **fields) -> None:
    with SessionLocal() as db:
        asset = db.get(VisualAsset, asset_id)
        spec = json.loads(asset.spec_json or "{}")
        spec["drawing"] = {**(spec.get("drawing") or {}), **fields, "updated": time.time()}
        asset.spec_json = json.dumps(spec, ensure_ascii=False)
        db.commit()


def make(asset_id: int) -> None:
    """Make the portrait of one photo (runs in a worker): the progress video and the pencil's path."""
    from app.documentary.drawing.portrait import PortraitDrawing, write_record

    try:
        with SessionLocal() as db:
            asset = db.get(VisualAsset, asset_id)
            photo, case_id, code = storage.resolve(asset.local_path), asset.case_id, asset.asset_code
        out = folder(case_id, code)
        _save(asset_id, status="drawing", progress=0.0)
        record = PortraitDrawing(photo).render(out, progress=lambda p: _save(asset_id, status="drawing", progress=round(p, 3)))
        write_record(out, record)
        _save(asset_id, status="ready", progress=1.0, video=storage.rel(Path(record["video"])), pen=storage.rel(Path(record["pen"])),
              seconds=record["seconds"], fps=record["fps"], frames=record["frames"], error=None)
    except Exception as e:  # a failed portrait is reported on its photo, never raised into the request that started it
        log.exception("portrait %s failed", asset_id)
        _save(asset_id, status="failed", error=f"{type(e).__name__}: {e}"[:300])
    finally:
        _running.discard(asset_id)


def start(asset: VisualAsset) -> bool:
    """Begin making this photo's portrait unless it is made or being made. Returns whether a worker was started."""
    if state_of(asset)["status"] in ("ready", "drawing") or asset.id in _running:
        return False
    _running.add(asset.id)
    _save(asset.id, status="drawing", progress=0.0)
    _pool.submit(make, asset.id)
    return True


def latest_record(db: Session, case_id: int) -> dict | None:
    """The planner's record of the portraits this film needs ({needed, speed, order, uncovered}), from its latest language plan."""
    for plan in db.query(VisualPlan).filter_by(case_id=case_id).order_by(VisualPlan.id.desc()).all():
        record = (json.loads(plan.validation_json or "{}").get("mvp") or {}).get("portraits")
        if record:
            return record
    return None
