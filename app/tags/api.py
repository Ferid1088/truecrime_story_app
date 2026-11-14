"""The routes of the Tags tab (documentary > Tags): the tag sets of a case in every configured language, the generation of one language or all of them, the owner's
check, save, approve and reject, the decision about a waiting regeneration, and the export of the approved tags. Generating is the only paid step; nothing is approved by it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Case
from app.services.budget import BudgetStopped
from app.tags import service as S

router = APIRouter()


class TagIn(BaseModel):
    text: str
    category: str | None = None
    evidence: list[str] = []


class TagsIn(BaseModel):
    tags: list[TagIn]


class GenerateIn(BaseModel):
    languages: list[str] | None = None
    force: bool = False


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _guard(call):
    try:
        return call()
    except (S.TagsError, BudgetStopped) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/api/cases/{case_id}/tags")
def tags_overview(case_id: int, db: Session = Depends(get_db)):
    return S.overview(db, _case(db, case_id))


@router.get("/api/cases/{case_id}/tags/export")
def tags_export(case_id: int, db: Session = Depends(get_db)):
    return S.export(db, _case(db, case_id))


@router.post("/api/cases/{case_id}/tags/generate")
async def generate_tags(case_id: int, body: GenerateIn | None = None, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    try:
        await S.generate(db, case, body.languages if body else None, force=bool(body and body.force))
    except (S.TagsError, BudgetStopped) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return S.overview(db, case)


@router.get("/api/cases/{case_id}/tags/{language}")
def tags_of(case_id: int, language: str, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    _guard(lambda: S.ensure_language(language))
    return S.view(db, case, language)


@router.post("/api/cases/{case_id}/tags/{language}/generate")
async def generate_language(case_id: int, language: str, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    try:
        await S.generate(db, case, [language], force=True)
    except (S.TagsError, BudgetStopped) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return S.view(db, case, language)


@router.post("/api/cases/{case_id}/tags/{language}/check")
def check_tags(case_id: int, language: str, body: TagsIn, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    return _guard(lambda: S.check(db, case, language, [t.model_dump() for t in body.tags]))


@router.put("/api/cases/{case_id}/tags/{language}")
def save_tags(case_id: int, language: str, body: TagsIn, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    _guard(lambda: S.save(db, case, language, [t.model_dump() for t in body.tags]))
    return S.view(db, case, language)


def _decide(action):
    def route(case_id: int, language: str, db: Session = Depends(get_db)):
        case = _case(db, case_id)
        _guard(lambda: action(db, case, language))
        return S.view(db, case, language)
    return route


for _path, _action in (("approve", S.approve), ("reject", S.reject), ("candidate/use", S.use_candidate), ("candidate/drop", S.drop_candidate)):
    router.add_api_route(f"/api/cases/{{case_id}}/tags/{{language}}/{_path}", _decide(_action), methods=["POST"])
