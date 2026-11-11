"""The routes of the cover review (documentary > Covers): the covers of a language with their proposals, the owner's change of a title or a scene description, the
scene picture the image model makes (paid, one press of a button, every attempt in the cost book), a preview of the cover, and the decision to approve or skip it. Only an approved cover goes into the upload plan later; the planner approves nothing, and a cover can only be approved
after its preview (of this very title and scene) was made."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.files import serve as serve_file
from app.covers import agents as CAG
from app.covers import compose as C
from app.covers import images as IM
from app.covers import proposals as P
from app.covers import service as SV
from app.db.base import get_db
from app.db.models import Case, CoverImage, CoverProposal
from app.documentary import storage
from app.providers.image.apimaster import ImageProviderError
from app.services.budget import BudgetStopped

router = APIRouter()


class ChangeIn(BaseModel):
    title: str | None = None
    scene: str | None = None


class ImageIn(BaseModel):
    again: bool = False                 # make a new picture also when the cover has one (paid)


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
    except (C.CoverError, SV.AV.AvatarError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


def image_view(db: Session, row: CoverProposal) -> dict | None:
    image = db.get(CoverImage, row.image_id) if row.image_id else None
    if image is None:
        return None
    return {"id": image.id, "url": f"/api/cases/{row.case_id}/covers/{row.language}/{row.cover_key}/image?image={image.id}", "attempt": image.attempt, "cost_usd": image.cost_usd,
            "cost_eur": image.cost_eur, "duration_s": round(image.duration_ms / 1000, 1), "image_tokens_out": image.image_tokens_out}


def cover_view(db: Session, case: Case, row: CoverProposal) -> dict:
    preview = preview_path(case.id, row.language, row)
    done = final_path(case.id, row.language, row.cover_key)
    return {**P.view(row), "image": image_view(db, row),
            "preview_url": f"/api/cases/{case.id}/covers/{row.language}/{row.cover_key}/preview?row={row.id}" if preview.exists() else None,
            "approved_url": f"/api/cases/{case.id}/covers/{row.language}/{row.cover_key}/file?row={row.id}" if row.status == "approved" and done.exists() else None}


def cost_book(db: Session, case_id: int, language: str) -> dict:
    """What the covers' pictures of a language cost so far, from the attempts (the numbers the model reported and the estimate booked in the ledger)."""
    attempts = db.query(CoverImage).filter(CoverImage.case_id == case_id, CoverImage.language == language).order_by(CoverImage.id).all()
    made = [a for a in attempts if a.ok]
    return {"model": ai_config.covers_settings().scene_image["model"], "attempts": len(attempts), "pictures": len(made), "failed": len(attempts) - len(made),
            "image_tokens_out": sum(a.image_tokens_out or 0 for a in made), "text_tokens_in": sum(a.text_tokens_in or 0 for a in made),
            "cost_usd": round(sum(a.cost_usd or 0 for a in made), 6), "cost_eur": round(sum(a.cost_eur or 0 for a in made), 6),
            "seconds": round(sum(a.duration_ms for a in attempts) / 1000, 1), "estimated": True, "rate_status": "unverified",
            "rows": [{"id": a.id, "cover_key": a.cover_key, "attempt": a.attempt, "ok": a.ok, "error": a.error, "text_tokens_in": a.text_tokens_in, "image_tokens_in": a.image_tokens_in,
                      "text_tokens_out": a.text_tokens_out, "image_tokens_out": a.image_tokens_out, "cost_usd": a.cost_usd, "cost_eur": a.cost_eur, "cost_source": a.cost_source,
                      "duration_s": round(a.duration_ms / 1000, 1)} for a in attempts]}


def overview(db: Session, case: Case, language: str) -> dict:
    found = P.current(db, case.id, language)
    cfg = ai_config.covers_settings()
    return {"case_id": case.id, "language": language, "look": cfg.themes[language].look, "expected": 1 + cfg.shorts_per_episode, "covers": [cover_view(db, case, r) for r in found.values()],
            "costs": cost_book(db, case.id, language), "platform_cover_ways": {k: v.get("cover_status") for k, v in cfg.platforms.items()}, "safe_zones": cfg.safe_zones["status"]}


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
    """The owner's title and/or scene description for a cover: a newer version (source owner). A changed scene needs a new picture; a changed title keeps it."""
    case = _case(db, case_id)
    row = _row(db, case, _language(language), key)
    title = row.title if body.title is None else body.title.strip()
    scene = row.scene_prompt if body.scene is None else body.scene.strip()
    if problems := C.title_issues(title):
        raise HTTPException(status_code=422, detail=problems[0])
    if scene and (problems := IM.scene_issues(scene)):
        raise HTTPException(status_code=422, detail=problems[0])
    new = CoverProposal(case_id=case.id, language=language, cover_key=key, kind=row.kind, version=row.version + 1, source="owner", status="proposed" if scene else "open",
                        title=title, scene_prompt=scene, image_id=row.image_id if scene == row.scene_prompt else None, confidence=None, reason="chosen by the owner",
                        validation_json=json.dumps({"issues": []}))
    db.add(new)
    db.commit()
    db.refresh(new)
    return cover_view(db, case, new)


@router.post("/api/cases/{case_id}/covers/{language}/{key}/image")
async def make_image(case_id: int, language: str, key: str, body: ImageIn | None = None, db: Session = Depends(get_db)):
    """The scene picture of a cover, made by the image model (paid; the one the cover has is kept unless `again`)."""
    case = _case(db, case_id)
    row = _row(db, case, _language(language), key)
    try:
        await IM.make_image(db, case, language, row, again=bool(body and body.again))
    except (ImageProviderError, C.CoverError, BudgetStopped) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return cover_view(db, case, row)


@router.get("/api/cases/{case_id}/covers/{language}/{key}/image")
def image_file(case_id: int, language: str, key: str, image: int, db: Session = Depends(get_db)):
    record = db.get(CoverImage, image)
    if record is None or (record.case_id, record.language, record.cover_key) != (case_id, language, key) or not record.file_path:
        raise HTTPException(status_code=404, detail="Image not found")
    return serve_file(storage.resolve(record.file_path), "image/png")


@router.get("/api/cases/{case_id}/covers/{language}/costs")
def costs(case_id: int, language: str, db: Session = Depends(get_db)):
    return cost_book(db, _case(db, case_id).id, _language(language))


@router.post("/api/cases/{case_id}/covers/{language}/{key}/preview")
def preview(case_id: int, language: str, key: str, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    row = _row(db, case, _language(language), key)
    made = _guard(lambda: SV.render_cover(db, case, language, row, preview_path(case.id, language, row), scale=ai_config.covers_settings().proposal["preview_scale"]))
    return {**cover_view(db, case, row), "problems": made["problems"]}


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
    if not row.title or row.image_id is None:
        raise HTTPException(status_code=409, detail="A cover needs a title and its scene picture before it can be approved")
    if not preview_path(case.id, language, row).exists():
        raise HTTPException(status_code=409, detail="Look at the preview of this title and scene first")
    out = final_path(case.id, language, key)
    made = _guard(lambda: SV.render_cover(db, case, language, row, out, scale=1.0))
    if made["problems"]:
        raise HTTPException(status_code=409, detail="; ".join(made["problems"]))
    row.status = "approved"
    row.validation_json = json.dumps({**json.loads(row.validation_json or "{}"), "file": storage.rel(out), "size": made["size"], "bytes": made["bytes"]})
    db.commit()
    return cover_view(db, case, row)


@router.post("/api/cases/{case_id}/covers/{language}/{key}/skip")
def skip(case_id: int, language: str, key: str, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    row = _row(db, case, _language(language), key)
    row.status = "skipped"
    db.commit()
    return cover_view(db, case, row)
