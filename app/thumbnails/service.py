"""Thumbnail stage: identity + brief + approved host + approved assets
-> composer -> critic -> a draft a human approves or rejects."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, Thumbnail, VisualAsset
from app.documentary import storage
from app.identity import titles as T
from app.thumbnails import brief as B
from app.thumbnails import compose as C
from app.thumbnails import critic as K
from app.thumbnails import hosts as H


class ThumbnailRefused(ValueError):
    pass


def _dir(case_id: int, language: str) -> Path:
    base = Path(ai_config.thumbnail.storage_dir)
    d = (base if base.is_absolute() else storage.ROOT / base) / str(case_id) / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_of(t: Thumbnail) -> Path | None:
    return storage.resolve(t.file_path) if t.file_path else None


def _asset_path(db: Session, asset_id: int) -> Path:
    a = db.get(VisualAsset, asset_id)
    return storage.resolve(a.local_path)


async def create_thumbnail(db: Session, case: Case, language: str, gen=None, **opts) -> Thumbnail:
    """Compose a new draft. Options: side, pose, primary_asset_id,
    secondary_asset_id, text, size. Raises BriefRefused / HostMissing."""
    brief = B.build_brief(db, case, language, **opts)
    host = next(a for a in H.hosts_for_outfit(brief["host"]["outfit_id"])
                if a.id == brief["host"]["reference_asset"])
    version = 1 + db.query(Thumbnail).filter(Thumbnail.case_id == case.id,
                                             Thumbnail.language == language).count()
    out = _dir(case.id, language) / f"v{version}.jpg"
    vis = brief["case_visuals"]
    layout = C.compose_thumbnail(
        brief, host.path, _asset_path(db, vis[0]["asset_id"]), out,
        _asset_path(db, vis[1]["asset_id"]) if len(vis) > 1 else None)
    report = await K.review(brief, layout, out, gen)
    row = Thumbnail(
        case_id=case.id, language=language, version=version, status="draft",
        brief_json=json.dumps(brief, ensure_ascii=False), critic_json=json.dumps(report, ensure_ascii=False),
        layout_json=json.dumps(layout), file_path=storage.rel(out),
        sha256=hashlib.sha256(out.read_bytes()).hexdigest(), outfit_id=host.outfit_id,
        host_asset_id=host.id, primary_asset_id=vis[0]["asset_id"],
        secondary_asset_id=vis[1]["asset_id"] if len(vis) > 1 else None,
        status_label=brief["resolution_label"])
    db.add(row)
    ident = T.get_identity(db, case.id, language)
    ident.host_outfit_id, ident.host_reference_asset = host.outfit_id, host.id
    ident.thumbnail_status_label = brief["resolution_label"]
    db.commit()
    db.refresh(row)
    return row


def decide(db: Session, t: Thumbnail, approve: bool, *, override: bool = False) -> Thumbnail:
    from app.utils import utc_now

    if approve:
        verdict = json.loads(t.critic_json or "{}").get("verdict")
        if verdict == "fail" and not override:
            raise ThumbnailRefused("the critic failed this thumbnail: "
                                   + "; ".join(json.loads(t.critic_json)["failures"][:4]))
        for other in db.query(Thumbnail).filter(
                Thumbnail.case_id == t.case_id, Thumbnail.language == t.language,
                Thumbnail.status == "approved", Thumbnail.id != t.id).all():
            other.status = "superseded"
    t.status = "approved" if approve else "rejected"
    t.decided_at = utc_now()
    db.commit()
    return t


def thumbnail_dict(t: Thumbnail) -> dict:
    brief = json.loads(t.brief_json or "{}")
    return {
        "id": t.id, "case_id": t.case_id, "language": t.language, "version": t.version,
        "status": t.status, "brief": brief, "critic": json.loads(t.critic_json or "{}"),
        "layout": json.loads(t.layout_json or "{}"), "outfit_id": t.outfit_id,
        "host_asset_id": t.host_asset_id, "primary_asset_id": t.primary_asset_id,
        "secondary_asset_id": t.secondary_asset_id, "status_label": t.status_label,
        "image_url": f"/api/thumbnails/{t.id}/image", "created_at": t.created_at,
        "decided_at": t.decided_at,
    }
