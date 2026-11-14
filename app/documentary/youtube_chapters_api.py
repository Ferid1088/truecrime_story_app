"""The routes of the Chapters tab (documentary > Chapters): the YouTube chapters of each language's film, a refresh of the times (deterministic, free: it also puts the list into the film's
description) and the chapters of a plan made before they existed (the chapter writer and auditor again, paid, labelled as such). Nothing here needs the owner's approval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.chapters import ChapterWriter
from app.db.base import get_db
from app.db.models import Case, EditorialBlueprint, ProductionScript, StoryVersion, Video
from app.documentary import youtube_chapters as YT
from app.documentary.chapters import latest_chapter_plan, plan_covers
from app.documentary.openings import master_of
from app.lifecycle.videos import apply_metadata
from app.services.budget import BudgetStopped
from app.tags.dossier import tagged_languages

router = APIRouter()


class LanguagesIn(BaseModel):
    languages: list[str] | None = None


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _languages(wanted: list[str] | None) -> list[str]:
    known = tagged_languages()
    unknown = [language for language in wanted or [] if language not in known]
    if unknown:
        raise HTTPException(status_code=409, detail=f"Not a language of this production: {', '.join(unknown)}")
    return wanted or known


def _video(db: Session, case_id: int, language: str) -> Video | None:
    return db.query(Video).filter(Video.case_id == case_id, Video.language == language).order_by(Video.id.desc()).first()


def overview(db: Session, case: Case, *, apply: bool = False) -> dict:
    """Every language's chapters. A rendered film without a stored list gets its list resolved now; `apply` also rebuilds the film's YouTube metadata."""
    out = []
    for language in tagged_languages():
        video = _video(db, case.id, language)
        if video is not None and (apply or not YT.stored(db, case.id, language)):
            if apply:
                apply_metadata(db, case, video)
            else:
                YT.resolve(db, case, video)
        out.append(YT.view(YT.stored(db, case.id, language), language))
    db.commit()
    return {"case_id": case.id, "languages": out}


@router.get("/api/cases/{case_id}/chapters/youtube")
def youtube_chapters(case_id: int, db: Session = Depends(get_db)):
    return overview(db, _case(db, case_id))


@router.post("/api/cases/{case_id}/chapters/youtube/refresh")
def refresh_chapters(case_id: int, db: Session = Depends(get_db)):
    return overview(db, _case(db, case_id), apply=True)


async def write_youtube_parts(db: Session, case: Case, languages: list[str], tasks: frozenset[str] | None = None, *, force: bool = False) -> list[str]:
    """The YouTube chapters and description, by the chapter writer, for the languages whose plan lacks them (a plan made before they existed), or the `tasks` asked for again when `force`.
    Returns the languages written. A budget stop is a 409."""
    done = []
    for language in languages:
        video = _video(db, case.id, language)
        ps = db.get(ProductionScript, video.production_script_id) if video and video.production_script_id else None
        bp = db.get(EditorialBlueprint, ps.blueprint_id) if ps and ps.blueprint_id else None
        if bp is None or (not force and plan_covers(latest_chapter_plan(db, bp.id), [language])):
            continue
        spoken = db.get(StoryVersion, ps.story_version_id)
        try:
            await ChapterWriter().add_youtube_parts(db, case, bp, master_of(db, spoken), language, spoken, latest_chapter_plan(db, bp.id), tasks if force else None)
        except BudgetStopped as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        done.append(language)
    return done


@router.post("/api/cases/{case_id}/chapters/youtube/plan")
async def plan_chapters(case_id: int, body: LanguagesIn | None = None, db: Session = Depends(get_db)):
    """What the chapter writer still has to add to a plan made before the YouTube chapters and descriptions existed (chapters and description in one call per language)."""
    case = _case(db, case_id)
    done = await write_youtube_parts(db, case, _languages(body.languages if body else None))
    return {**overview(db, case, apply=True), "planned": done}
