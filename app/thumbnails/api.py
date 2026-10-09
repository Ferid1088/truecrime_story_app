"""Thumbnail API: candidates for the pictures, compose, review, decide."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Case, Thumbnail
from app.thumbnails import brief as B
from app.thumbnails import hosts as H
from app.thumbnails import selection as S
from app.thumbnails import service as SV

router = APIRouter(tags=["thumbnails"])
Lang = Literal["en", "de", "fa", "ar"]


def _gen():
    """Vision critic provider, or None (deterministic checks still run)."""
    try:
        from app.providers.generation import get_generation_provider

        gen = get_generation_provider()
        return gen if gen.is_configured() else None
    except Exception:  # noqa: BLE001
        return None


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


class ComposeRequest(BaseModel):
    language: Lang
    side: Literal["left", "right"] | None = None
    pose: str | None = None
    size: Literal["large", "xl"] = "large"
    primary_asset_id: int | None = None
    secondary_asset_id: int | None = None
    text: str = Field(default="", max_length=80)


class DecisionRequest(BaseModel):
    approve: bool
    override: bool = False


@router.get("/api/cases/{case_id}/thumbnails")
def list_thumbnails(case_id: int, language: Lang | None = None, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    q = db.query(Thumbnail).filter(Thumbnail.case_id == case.id)
    if language:
        q = q.filter(Thumbnail.language == language)
    return [SV.thumbnail_dict(t) for t in q.order_by(Thumbnail.id.desc()).all()]


@router.get("/api/cases/{case_id}/thumbnails/options")
def thumbnail_options(case_id: int, language: Lang, db: Session = Depends(get_db)):
    """What can go on the thumbnail: usable real pictures (ranked), the
    rejected ones with the reason, and the approved host poses."""
    case = _case(db, case_id)
    ok, bad = S.rank_assets(db, case)
    from app.identity.titles import get_identity

    try:
        outfit = H.outfit_for_episode(db, case.id, language, get_identity(db, case.id, language))
        poses = [{"id": a.id, "pose": a.pose, "facing": a.facing} for a in H.hosts_for_outfit(outfit)]
        host_error = None
    except H.HostMissing as e:
        outfit, poses, host_error = None, [], str(e)
    pick = lambda r: {"asset_id": r["id"], "code": r["code"], "kind": r["kind"],  # noqa: E731
                      "title": r["asset"].title, "rights": r["asset"].rights_status,
                      "tier": r["asset"].relevance_tier,
                      "thumb": f"/api/visuals/{r['asset'].id}/file?thumb=1"}
    return {"outfit_id": outfit, "host_poses": poses, "host_error": host_error,
            "usable": [pick(r) for r in ok],
            "rejected": [{**pick(r), "reason": r["reason"]} for r in bad]}


@router.post("/api/cases/{case_id}/thumbnails")
async def compose(case_id: int, req: ComposeRequest, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    try:
        t = await SV.create_thumbnail(
            db, case, req.language, _gen(), side=req.side, pose=req.pose, size=req.size,
            primary_asset_id=req.primary_asset_id, secondary_asset_id=req.secondary_asset_id,
            text=req.text)
    except (B.BriefRefused, H.HostMissing) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return SV.thumbnail_dict(t)


@router.post("/api/thumbnails/{thumbnail_id}/decision")
def decide(thumbnail_id: int, req: DecisionRequest, db: Session = Depends(get_db)):
    t = db.get(Thumbnail, thumbnail_id)
    if not t:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    try:
        return SV.thumbnail_dict(SV.decide(db, t, req.approve, override=req.override))
    except SV.ThumbnailRefused as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/api/thumbnails/{thumbnail_id}/image")
def image(thumbnail_id: int, db: Session = Depends(get_db)):
    t = db.get(Thumbnail, thumbnail_id)
    p = SV.file_of(t) if t else None
    if not p or not p.exists():
        raise HTTPException(status_code=404, detail="Thumbnail image not found")
    return FileResponse(p, media_type="image/jpeg")
