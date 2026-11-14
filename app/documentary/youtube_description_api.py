"""The routes of the Descriptions tab (documentary > Descriptions): the description of each language's film, the owner's own text (kept, never replaced by the app), going back to the automatic text,
rebuilding it from what the application holds (free) and writing the narrative again (the chapter writer and auditor, paid, on request). Nothing here needs the owner's approval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Case, Video
from app.documentary import youtube_description as YD
from app.documentary.youtube_chapters_api import write_youtube_parts
from app.lifecycle.videos import apply_metadata
from app.tags.dossier import tagged_languages

router = APIRouter()


class EditIn(BaseModel):
    text: str | None = None


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _language(language: str) -> str:
    if language not in tagged_languages():
        raise HTTPException(status_code=409, detail=f"Not a language of this production: {language}")
    return language


def _video(db: Session, case_id: int, language: str) -> Video | None:
    return db.query(Video).filter(Video.case_id == case_id, Video.language == language).order_by(Video.id.desc()).first()


def overview(db: Session, case: Case, *, apply: bool = False) -> dict:
    """Every language's description. A rendered film without a stored one gets it resolved now; `apply` also rebuilds the film's YouTube metadata."""
    out = []
    for language in tagged_languages():
        video = _video(db, case.id, language)
        if video is not None and (apply or not YD.stored(db, case.id, language)):
            if apply:
                apply_metadata(db, case, video)
            else:
                YD.resolve(db, case, video)
        out.append(YD.view(YD.stored(db, case.id, language), language))
    db.commit()
    return {"case_id": case.id, "languages": out}


@router.get("/api/cases/{case_id}/descriptions")
def descriptions(case_id: int, db: Session = Depends(get_db)):
    return overview(db, _case(db, case_id))


@router.post("/api/cases/{case_id}/descriptions/refresh")
def refresh_descriptions(case_id: int, db: Session = Depends(get_db)):
    return overview(db, _case(db, case_id), apply=True)


@router.put("/api/cases/{case_id}/descriptions/{language}")
def edit_description(case_id: int, language: str, body: EditIn, db: Session = Depends(get_db)):
    """The owner's own text; an empty text goes back to the automatic one. The film's YouTube description follows at once."""
    case = _case(db, case_id)
    video = _video(db, case.id, _language(language))
    if video is None:
        raise HTTPException(status_code=409, detail="The film is not rendered yet")
    YD.save_edit(db, YD.resolve(db, case, video), body.text)
    return overview(db, case, apply=True)


@router.post("/api/cases/{case_id}/descriptions/{language}/regenerate")
async def regenerate_description(case_id: int, language: str, db: Session = Depends(get_db)):
    """The narrative written again by the chapter writer and auditor (paid). The owner's own text, if there is one, stays the text in use; the new automatic text is shown beside it."""
    case = _case(db, case_id)
    if await write_youtube_parts(db, case, [_language(language)], frozenset({"description"}), force=True) == []:
        raise HTTPException(status_code=409, detail="The film has no chapter plan to write the description in")
    return overview(db, case, apply=True)
