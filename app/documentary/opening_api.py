"""The routes of the Opening tab (documentary > Opening): the first seconds of each language's film (the hook, the context text and the music brief) that come before the host's first appearance (S1): what was written,
the voice as it was assembled, the pictures' scenes and the checks. Read only: the voice and the texts are reviewed in the Voice Review and Farsi review tabs. An opening exists for this tab once its voice has been made: a language
that has only the written text (or nothing) is not listed."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.files import serve as serve_file
from app.db.base import get_db
from app.db.models import Case, FilmOpening
from app.documentary import storage
from app.documentary.opening_block import latest_opening
from app.tags.dossier import tagged_languages

router = APIRouter()
REVALIDATE = {"Cache-Control": "no-cache"}          # the opening is made again (another music, another speed): the browser asks every time and the file tag says whether it changed


def _case(db: Session, case_id: int) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def _file(row: FilmOpening, rel: str | None):
    path = storage.resolve(rel) if rel else None
    return path if path is not None and path.is_file() else None


def view(row: FilmOpening) -> dict:
    """One language's opening as the tab shows it. Pictures and the audio are links only when the file exists."""
    opening, media, validation = json.loads(row.opening_json or "{}"), json.loads(row.media_json or "{}"), json.loads(row.validation_json or "{}")
    base = f"/api/cases/{row.case_id}/openings/{row.language}"
    audio = media.get("audio") or {}
    pictures = media.get("pictures") or []
    chosen = opening.get("chosen")
    return {"language": row.language, "kind": row.kind, "status": row.status, "version": row.version, "created_at": row.created_at.isoformat() if row.created_at else None,
            "hook": opening.get("hook", ""), "context": opening.get("context", ""), "music": opening.get("music") or {}, "model": row.generation_model,
            "candidates": [{**c, "chosen": i == chosen} for i, c in enumerate(opening.get("candidates") or [])],
            "scenes": [{"text": s, "picture_url": f"{base}/picture/{i}" if i < len(pictures) and _file(row, pictures[i].get("path")) else None} for i, s in enumerate(opening.get("scenes") or [])],
            "audio": {"url": f"{base}/audio?v={(audio.get('sha256') or '')[:12]}", "seconds": audio.get("seconds"), "hook": audio.get("hook"), "context": audio.get("context")} if _file(row, audio.get("path")) else None,
            "issues": validation.get("issues", [])}


@router.get("/api/cases/{case_id}/openings")
def openings(case_id: int, db: Session = Depends(get_db)):
    case = _case(db, case_id)
    made = [view(row) for row in (latest_opening(db, case.id, language) for language in tagged_languages()) if row is not None]
    return {"case_id": case.id, "languages": [v for v in made if v["audio"] is not None]}


def _row(db: Session, case_id: int, language: str) -> FilmOpening:
    row = latest_opening(db, _case(db, case_id).id, language)
    if row is None:
        raise HTTPException(status_code=404, detail="This language has no opening")
    return row


@router.get("/api/cases/{case_id}/openings/{language}/audio")
def opening_audio(case_id: int, language: str, db: Session = Depends(get_db)):
    row = _row(db, case_id, language)
    return serve_file(_file(row, (json.loads(row.media_json or "{}").get("audio") or {}).get("path")), "audio/wav", missing="The opening has no voice yet", headers=REVALIDATE)


@router.get("/api/cases/{case_id}/openings/{language}/picture/{index}")
def opening_picture(case_id: int, language: str, index: int, db: Session = Depends(get_db)):
    row = _row(db, case_id, language)
    pictures = json.loads(row.media_json or "{}").get("pictures") or []
    return serve_file(_file(row, pictures[index].get("path")) if 0 <= index < len(pictures) else None, "image/png", missing="No such picture")
