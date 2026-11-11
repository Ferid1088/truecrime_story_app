"""The routes of the cover review (documentary > Covers): the covers of a language with their proposals, the owner's change of a title or a scene, a preview of the
cover, and the decision to approve or skip it. Only an approved cover goes into the upload plan later; the planner approves nothing, and a cover can only be approved
after its preview (of this very title and scene) was made."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.files import serve as serve_file
from app.covers import agents as CAG
from app.covers import compose as C
from app.covers import proposals as P
from app.covers import scene as SC
from app.covers import service as SV
from app.db.base import get_db
from app.db.models import Case, CoverProposal
from app.documentary import storage

router = APIRouter()


class ChangeIn(BaseModel):
    title: str | None = None
    scene_code: str | None = None
    clear_scene: bool = False


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _language(language: str) -> str:
    if language not in ai_config.covers_settings().themes:
        raise HTTPException(status_code=404, detail=f"No cover theme for {language}")
    return language


def _row(db: Session, case: Case, language: str, key: str) -> CoverProposal:
    row = P.current(db, case.id, language).get(key)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No proposal for the cover {key}")
    return row


def preview_path(case_id: int, language: str, row: CoverProposal) -> Path:
    return SV.covers_dir(case_id, language) / f"preview_{row.cover_key}_{row.id}.jpg"


def final_path(case_id: int, language: str, key: str) -> Path:
    return SV.covers_dir(case_id, language) / ("long.jpg" if key == "long" else f"short_{key}.jpg")


def _guard(call):
    try:
        return call()
    except (C.CoverError, SC.SceneMissing, SV.AV.AvatarError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


def cover_view(case: Case, row: CoverProposal) -> dict:
    preview = preview_path(case.id, row.language, row)
    done = final_path(case.id, row.language, row.cover_key)
    return {**P.view(row), "preview_url": f"/api/cases/{case.id}/covers/{row.language}/{row.cover_key}/preview?row={row.id}" if preview.exists() else None,
            "approved_url": f"/api/cases/{case.id}/covers/{row.language}/{row.cover_key}/file?row={row.id}" if row.status == "approved" and done.exists() else None}


def overview(db: Session, case: Case, language: str) -> dict:
    found = P.current(db, case.id, language)
    options = SC.candidates(db, case, language)
    return {"case_id": case.id, "language": language, "theme": ai_config.covers_settings().themes[language].signature, "expected": 1 + ai_config.covers_settings().shorts_per_episode,
            "covers": [cover_view(case, r) for r in found.values()],
            "candidates": [{"code": o["code"], "kind": o["kind"], "what": o["what"], "image_url": f"/api/cases/{case.id}/covers/{language}/scenes/{o['code']}/image"} for o in options],
            "platform_cover_ways": {k: v.get("cover_status") for k, v in ai_config.covers_settings().platforms.items()}, "safe_zones": ai_config.covers_settings().safe_zones["status"]}


@router.get("/api/cases/{case_id}/covers/summary")
def summary(case_id: int, db: Session = Depends(get_db)):
    """For every language: how many of its covers are proposed, open, approved or skipped, against the 1 + shorts_per_episode it has."""
    case = _case(db, case_id)
    expected = 1 + ai_config.covers_settings().shorts_per_episode
    out = {}
    for language in ai_config.covers_settings().themes:
        rows = P.current(db, case.id, language).values()
        counts = {s: sum(1 for r in rows if r.status == s) for s in P.STATUSES}
        out[language] = {**counts, "missing": expected - len(list(rows))}
    return {"expected_per_language": expected, "languages": out}


@router.get("/api/cases/{case_id}/covers/{language}")
def get_covers(case_id: int, language: str, db: Session = Depends(get_db)):
    return overview(db, _case(db, case_id), _language(language))


@router.post("/api/cases/{case_id}/covers/{language}/propose")
async def propose(case_id: int, language: str, db: Session = Depends(get_db)):
    """The planner's proposals for this language (new versions; the owner's approved covers stay approved until the owner changes them)."""
    case = _case(db, case_id)
    try:
        await CAG.CoverPlanner().propose(db, case, _language(language))
    except P.ProposalError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return overview(db, case, language)


@router.put("/api/cases/{case_id}/covers/{language}/{key}")
def change(case_id: int, language: str, key: str, body: ChangeIn, db: Session = Depends(get_db)):
    """The owner's title and/or scene for a cover: a new version (source owner). A scene must be one of the film's candidates."""
    case = _case(db, case_id)
    row = _row(db, case, _language(language), key)
    title = row.title if body.title is None else body.title.strip()
    scene = None if body.clear_scene else (body.scene_code if body.scene_code is not None else row.scene_code)
    if problems := C.title_issues(title):
        raise HTTPException(status_code=422, detail=problems[0])
    if scene is not None and scene not in {o["code"] for o in SC.candidates(db, case, language)}:
        raise HTTPException(status_code=422, detail=f"{scene} is not a picture this film shows")
    new = CoverProposal(case_id=case.id, language=language, cover_key=key, kind=row.kind, version=row.version + 1, source="owner", status="proposed" if scene else "open",
                        title=title, scene_code=scene, confidence=None, reason="chosen by the owner", validation_json=json.dumps({"issues": []}))
    db.add(new)
    db.commit()
    db.refresh(new)
    return cover_view(case, new)


@router.post("/api/cases/{case_id}/covers/{language}/{key}/preview")
def preview(case_id: int, language: str, key: str, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    row = _row(db, case, _language(language), key)
    made = _guard(lambda: SV.render_cover(db, case, language, row, preview_path(case.id, language, row), scale=ai_config.covers_settings().proposal["preview_scale"]))
    return {**cover_view(case, row), "problems": made["problems"], "signature": made["signature"]}


@router.get("/api/cases/{case_id}/covers/{language}/{key}/preview")
def preview_file(case_id: int, language: str, key: str, row: int, db: Session = Depends(get_db)):
    proposal = db.get(CoverProposal, row)
    if proposal is None or (proposal.case_id, proposal.language, proposal.cover_key) != (case_id, language, key):
        raise HTTPException(status_code=404, detail="Preview not found")
    return serve_file(preview_path(case_id, language, proposal), "image/jpeg")


@router.get("/api/cases/{case_id}/covers/{language}/{key}/file")
def approved_file(case_id: int, language: str, key: str, row: int, db: Session = Depends(get_db)):
    proposal = db.get(CoverProposal, row)
    if proposal is None or proposal.status != "approved" or (proposal.case_id, proposal.language, proposal.cover_key) != (case_id, language, key):
        raise HTTPException(status_code=404, detail="Approved cover not found")
    return serve_file(final_path(case_id, language, key), "image/jpeg")


@router.post("/api/cases/{case_id}/covers/{language}/{key}/approve")
def approve(case_id: int, language: str, key: str, db: Session = Depends(get_db)):
    """Approves the cover as it is: the title and scene whose preview the owner has seen; the cover is then made in its full size."""
    case = _case(db, case_id)
    row = _row(db, case, _language(language), key)
    if not row.title or not row.scene_code:
        raise HTTPException(status_code=409, detail="A cover needs a title and a scene before it can be approved")
    if not preview_path(case.id, language, row).exists():
        raise HTTPException(status_code=409, detail="Look at the preview of this title and scene first")
    out = final_path(case.id, language, key)
    made = _guard(lambda: SV.render_cover(db, case, language, row, out, scale=1.0))
    if made["problems"]:
        raise HTTPException(status_code=409, detail="; ".join(made["problems"]))
    row.status = "approved"
    row.validation_json = json.dumps({**json.loads(row.validation_json or "{}"), "file": storage.rel(out), "size": made["size"], "bytes": made["bytes"]})
    db.commit()
    return cover_view(case, row)


@router.post("/api/cases/{case_id}/covers/{language}/{key}/skip")
def skip(case_id: int, language: str, key: str, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    row = _row(db, case, _language(language), key)
    row.status = "skipped"
    db.commit()
    return cover_view(case, row)


@router.get("/api/cases/{case_id}/covers/{language}/scenes/{code}/image")
def scene_image(case_id: int, language: str, code: str, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    option = next((o for o in SC.candidates(db, case, _language(language)) if o["code"] == code), None)
    if option is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    thumb = SV.covers_dir(case.id, language) / "thumbs" / f"{code}.jpg"
    if not thumb.exists():
        thumb.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(option["path"]) as picture:
            picture.thumbnail((ai_config.covers_settings().proposal["thumb_px"],) * 2)
            picture.convert("RGB").save(thumb, "JPEG", quality=82)
    return serve_file(thumb, "image/jpeg")
