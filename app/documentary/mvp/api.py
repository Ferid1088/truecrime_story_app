"""Owner-review routes of the Main Visual Planner."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import MvpFilmReview
from app.documentary.mvp import review as RV

router = APIRouter(tags=["main-visual-planner"])


class Decision(BaseModel):
    decision: str
    note: str | None = None
    render_sha256: str | None = None


def review_dict(row: MvpFilmReview) -> dict:
    return {"id": row.id, "case_id": row.case_id, "language": row.language, "state": row.state,
            "production_script_id": row.production_script_id, "render_path": row.render_path,
            "render_sha256": row.render_sha256, "validation": json.loads(row.validation_json or "{}"),
            "owner_note": row.owner_note, "created_at": row.created_at, "decided_at": row.decided_at}


@router.get("/api/cases/{case_id}/mvp/reviews")
def list_reviews(case_id: int, db: Session = Depends(get_db)):
    rows = db.query(MvpFilmReview).filter_by(case_id=case_id).order_by(MvpFilmReview.id.desc()).all()
    return [review_dict(r) for r in rows]


@router.post("/api/mvp/reviews/{review_id}/decision")
def decide(review_id: int, payload: Decision, db: Session = Depends(get_db)):
    row = db.get(MvpFilmReview, review_id)
    if row is None:
        raise HTTPException(404, "Review not found")
    try:
        return review_dict(RV.decide(db, row, payload.decision, payload.note, payload.render_sha256))
    except RV.ReviewError as e:
        raise HTTPException(409, str(e)) from e
