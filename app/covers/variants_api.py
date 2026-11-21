"""The routes of the three-shape covers of a Short in the cover review: what exists, a new render (no paid call), the approval or rejection of one shape's version, the image of a version.
Approving a cover with a photograph of a real person needs the explicit acknowledgement that an editorial approval is not copyright permission."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.files import serve as serve_file
from app.covers import triptych as T
from app.covers import photo_gate as PG
from app.covers import variants as V
from app.db.base import get_db
from app.db.models import Case, CoverVariant
from app.documentary import storage

router = APIRouter(tags=["cover-variants"])


class RenderBody(BaseModel):
    shapes: list[str] | None = None
    highlight: str | None = None
    subject_entity: str | None = None
    use_photo: bool = True
    photo_asset_id: int | None = None
    subject_name: str | None = None


class ApproveBody(BaseModel):
    acknowledge_rights: bool = False


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _refuse(exc: V.VariantError) -> HTTPException:
    return HTTPException(status_code=404 if exc.reason == "not_found" else 409, detail={"message": str(exc), "reason": exc.reason})


@router.get("/api/cases/{case_id}/covers/{language}/{key}/shapes")
def shapes(case_id: int, language: str, key: str, db: Session = Depends(get_db)):
    return V.overview(db, _case(db, case_id), language, key)


@router.post("/api/cases/{case_id}/covers/{language}/{key}/shapes")
def render(case_id: int, language: str, key: str, body: RenderBody, db: Session = Depends(get_db)):
    """New versions of the chosen shapes, drawn in code from real parts: nothing is paid for and nothing already approved is changed."""
    case = _case(db, case_id)
    try:
        V.render(db, case, language, key, shapes=body.shapes, highlight=body.highlight, subject_entity=body.subject_entity, use_photo=body.use_photo, photo_asset_id=body.photo_asset_id, subject_name=body.subject_name)
    except (V.VariantError, T.CoverError) as exc:
        raise _refuse(exc if isinstance(exc, V.VariantError) else V.VariantError("cover_error", str(exc))) from exc
    return V.overview(db, case, language, key)


@router.post("/api/cover-variants/{variant_id}/approve")
def approve(variant_id: int, body: ApproveBody, db: Session = Depends(get_db)):
    try:
        row = V.approve(db, variant_id, acknowledge_rights=body.acknowledge_rights)
    except V.VariantError as exc:
        raise _refuse(exc) from exc
    return V.view(row)


@router.post("/api/cover-variants/{variant_id}/reject")
def reject(variant_id: int, db: Session = Depends(get_db)):
    try:
        return V.view(V.reject(db, variant_id))
    except V.VariantError as exc:
        raise _refuse(exc) from exc


@router.get("/api/cover-variants/{variant_id}/image")
def image(variant_id: int, db: Session = Depends(get_db)):
    row = db.get(CoverVariant, variant_id)
    path = storage.resolve(row.file_path) if row is not None and row.file_path else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return serve_file(path, "image/jpeg")


class FinishBody(BaseModel):
    approved_by: str = "owner"


@router.get("/api/cases/{case_id}/cover-photos/finished")
def cover_photos_status(case_id: int, db: Session = Depends(get_db)):
    _case(db, case_id)
    return PG.status(db, case_id)


@router.post("/api/cases/{case_id}/cover-photos/finished")
def cover_photos_finish(case_id: int, body: FinishBody, db: Session = Depends(get_db)):
    """The owner is finished with the photographs for the covers: records it with the approved photographs and makes every cover with them (no paid call; new proposed versions only)."""
    try:
        return PG.finish(db, _case(db, case_id), body.approved_by)
    except PG.PhotoGateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/api/cases/{case_id}/cover-photos/finished")
def cover_photos_withdraw(case_id: int, db: Session = Depends(get_db)):
    _case(db, case_id)
    return PG.withdraw(db, case_id)
