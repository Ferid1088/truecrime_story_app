"""The outfit library API: the folder mirror plus the manual rescan."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import OutfitItem
from app.outfits import library as outfit_library

router = APIRouter()


@router.get("/api/outfit-library")
def outfit_library_view(db: Session = Depends(get_db)):
    return outfit_library.library_view(db)


@router.post("/api/outfit-library/rescan")
def rescan_outfit_library(db: Session = Depends(get_db)):
    return outfit_library.scan_library(db, trigger="manual")


@router.get("/api/outfit-library/items/{item_id}/image")
def outfit_library_image(item_id: int, db: Session = Depends(get_db)):
    row = db.get(OutfitItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="outfit photo not found")
    path = Path(row.file_path)
    if not path.is_absolute():
        path = outfit_library.ROOT / path
    if not path.is_file() or outfit_library.OUTFIT_ROOT not in path.resolve().parents:
        raise HTTPException(status_code=404, detail="photo missing on disk")
    return FileResponse(path)


@router.post("/api/outfit-library/{studio}/photos")
async def add_outfit_photo(studio: str, outfit: str = Form(...), situation: str = Form(...),
                           photo: UploadFile = File(...), db: Session = Depends(get_db)):
    """Add a photo to an outfit of a language (a new outfit when the name is new)."""
    try:
        row = outfit_library.add_photo(db, studio, outfit, situation, await photo.read())
    except outfit_library.AddPhotoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": row.id, "ref": f"{row.studio}/{row.outfit_slug}", "situation": row.situation, "stance": row.stance,
            "issues": json.loads(row.issues_json or "[]")}
