"""HeyGen costs in the ledger (provider_calls).

HeyGen charges by generated seconds and its API reports no cost, so each video is booked as an
ESTIMATE from the price list (pricing.json, heygen.eur_per_minute): one entry per video, keyed by
the provider's job id so booking again never adds a second entry."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.db.models import HostScene, ProviderCall
from app.documentary import storage
from app.services.budget import BudgetService
from app.services.cost_model import load_pricing

log = logging.getLogger(__name__)
STAGE = "heygen:avatar"
MODEL = "heygen_avatar_iii"


def video_seconds(scene: HostScene) -> float | None:
    """The seconds HeyGen generated: its own figure when it reported one, else the file's."""
    reported = json.loads(scene.avatar_meta_json or "{}").get("duration")
    if reported:
        return float(reported)
    path = storage.resolve(scene.avatar_video_path) if scene.avatar_video_path else None
    if path is None or not path.exists():
        return None
    from app.documentary import audio as A

    return A.probe_duration(path)


def book_video(db: Session, scene: HostScene) -> ProviderCall | None:
    """The estimated cost of this scene's video, once. None while there is no finished video."""
    if not scene.provider_job_id or not scene.avatar_video_path:
        return None
    key = f"heygen:{scene.provider_job_id}"
    existing = (db.query(ProviderCall)
                .filter(ProviderCall.stage == STAGE, ProviderCall.request_id == key).first())
    if existing is not None:
        return existing
    seconds = video_seconds(scene)
    if not seconds:
        return None
    price = float(load_pricing()["heygen"]["eur_per_minute"])
    return BudgetService(db).record(
        case_id=scene.case_id, stage=STAGE, model=MODEL, units=round(seconds, 3),
        cost_eur=round(seconds / 60 * price, 6), estimated=True, request_id=key,
        cost_source="estimate")


def book_existing(db: Session) -> list[ProviderCall]:
    """Book every finished video that has no entry yet (the ones made before this existed)."""
    rows = []
    for scene in (db.query(HostScene).filter(HostScene.avatar_video_path.isnot(None))
                  .order_by(HostScene.id)):
        row = book_video(db, scene)
        if row is not None:
            rows.append(row)
    return rows
