"""The routes of the hand review of the host's voice (documentary > Pronunciation > Host voice review). The same review as the narration's, without the two
things that belong to the narration alone: there is no route that assembles and none that approves."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.files import serve as serve_file
from app.db.base import get_db
from app.db.models import StoryVersion, VoiceReview
from app.documentary.block_review import api as A
from app.documentary.block_review import edits as E
from app.documentary.block_review import host_review as HR
from app.documentary.block_review import service as S

router = APIRouter()
BASE = "host-voice-review"


def _review(db: Session, version: StoryVersion) -> VoiceReview:
    review = HR.current_review(db, version)
    if review is None:
        raise HTTPException(status_code=409, detail="Open the review of the host voice first")
    return review


def overview(db: Session, version: StoryVersion, review: VoiceReview) -> dict:
    blocks = S.blocks_of(db, review)
    return {"exists": True, "kind": HR.HOST, "version_id": version.id, "language": review.language, "status": "open", "assembled_current": False, "assembled_at": None,
            "approved": False, "assembled_audio_url": None, "corrected_blocks": sum(1 for b in blocks if b.edited),
            "to_send": sum(1 for b in blocks if S.needs_sending(db, b)), "blocks": [A.block_view(db, version, b, BASE) for b in blocks]}


@router.get("/api/documentary/versions/{version_id}/host-voice-review")
def get_review(version_id: int, db: Session = Depends(get_db)):
    version = A._version(db, version_id)
    review = HR.current_review(db, version)
    if review is None:
        return {"exists": False, "kind": HR.HOST, "version_id": version.id, "can_open": HR.allowed(version.language or "") and bool(HR.scenes_of(db, version))}
    return overview(db, version, review)


@router.post("/api/documentary/versions/{version_id}/host-voice-review/open")
def open_review(version_id: int, db: Session = Depends(get_db)):
    version = A._version(db, version_id)
    return overview(db, version, A._guard(lambda: HR.open_review(db, version)))


@router.put("/api/documentary/versions/{version_id}/host-voice-review/blocks/{block_id}")
def save_edit(version_id: int, block_id: str, body: A.EditIn, db: Session = Depends(get_db)):
    version = A._version(db, version_id)
    review = _review(db, version)
    try:
        block = A._guard(lambda: S.save_edit(db, review, block_id, body.text, [E.WordEdit(w.n, w.surface, w.finglish) for w in body.words]))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return A.block_view(db, version, block, BASE)


@router.post("/api/documentary/versions/{version_id}/host-voice-review/blocks/{block_id}/send")
async def send_block(version_id: int, block_id: str, body: A.SendIn | None = None, db: Session = Depends(get_db)):
    version = A._version(db, version_id)
    review = _review(db, version)
    try:
        await S.send_block(db, review, block_id, again=bool(body and body.again))
    except S.ReviewError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return A.block_view(db, version, S.block_of(db, review, block_id), BASE)


@router.post("/api/documentary/versions/{version_id}/host-voice-review/replace")
def replace_everywhere(version_id: int, body: A.ReplaceIn, db: Session = Depends(get_db)):
    version = A._version(db, version_id)
    review = _review(db, version)
    matches = A._guard(lambda: S.replace_everywhere(db, review, body.find, body.replace, body.apply))
    return {"applied": body.apply, "blocks_found": len(matches), "places_found": sum(m["count"] for m in matches), "matches": matches,
            **(overview(db, version, review) if body.apply else {})}


@router.post("/api/documentary/versions/{version_id}/host-voice-review/send-all")
async def send_all(version_id: int, db: Session = Depends(get_db)):
    """Voices every corrected host block that has not been sent yet, one after the other (a block that fails stays a draft and is reported)."""
    version = A._version(db, version_id)
    review = _review(db, version)
    sent, failed = [], []
    for block in S.blocks_of(db, review):
        if not S.needs_sending(db, block):
            continue
        try:
            await S.send_block(db, review, block.block_id)
            sent.append(block.block_id)
        except Exception as e:  # noqa: BLE001 -- one block failing must not stop the others; it is reported
            db.rollback()
            failed.append({"block_id": block.block_id, "error": str(e)[:300]})
    return {"sent": sent, "failed": failed, **overview(db, version, review)}


@router.post("/api/documentary/versions/{version_id}/host-voice-review/blocks/{block_id}/takes/{take_no}/activate")
def activate(version_id: int, block_id: str, take_no: int, db: Session = Depends(get_db)):
    version = A._version(db, version_id)
    review = _review(db, version)
    _ = A._guard(lambda: S.activate(db, review, block_id, take_no))
    return A.block_view(db, version, S.block_of(db, review, block_id), BASE)


@router.get("/api/documentary/versions/{version_id}/host-voice-review/blocks/{block_id}/takes/{take_no}/audio")
def take_audio(version_id: int, block_id: str, take_no: int, db: Session = Depends(get_db)):
    version = A._version(db, version_id)
    block = S.block_of(db, _review(db, version), block_id)
    take = next((t for t in S.takes_of(db, block) if t.take_no == take_no), None)
    path = HR.audio_path(db, block, take) if take else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Take not found")
    return serve_file(path, "audio/mpeg")
