"""The routes of the text review (documentary > Persian texts): the texts of a language, the owner's correction of each, and the release that lets the covers be made."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.covers import text_gate as TG
from app.db.base import get_db
from app.db.models import Case

router = APIRouter(tags=["cover-texts"])


class TextIn(BaseModel):
    text: str


class ReleaseIn(BaseModel):
    approved_by: str = "owner"


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/api/cases/{case_id}/texts/{language}")
def texts(case_id: int, language: str, db: Session = Depends(get_db)):
    return TG.status(db, _case(db, case_id), language)


@router.put("/api/cases/{case_id}/texts/{language}/{kind}/{key}")
def correct(case_id: int, language: str, kind: str, key: str, body: TextIn, db: Session = Depends(get_db)):
    try:
        return TG.edit(db, _case(db, case_id), language, kind, key, body.text)
    except TG.TextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/cases/{case_id}/texts/{language}/release")
def release(case_id: int, language: str, body: ReleaseIn, db: Session = Depends(get_db)):
    try:
        return TG.release(db, _case(db, case_id), language, body.approved_by)
    except TG.TextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/api/cases/{case_id}/texts/{language}/release")
def withdraw(case_id: int, language: str, db: Session = Depends(get_db)):
    return TG.withdraw(db, _case(db, case_id), language)
