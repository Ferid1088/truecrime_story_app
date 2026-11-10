"""The routes of the hand review of a voiced narration (documentary > Pronunciation > Narration preview, and the shortcut on the Monitoring tab)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.core.files import serve as serve_file
from app.db.base import get_db
from app.db.models import StoryVersion, VoiceBlock, VoiceBlockTake, VoiceReview
from app.documentary import voice_review as VRV
from app.documentary.block_review import edits as E
from app.documentary.block_review import service as S
from app.documentary.words import tokenize
from app.documentary.voice_render import VoiceRenderer

router = APIRouter()


class WordIn(BaseModel):
    n: int = Field(ge=0)
    surface: str | None = None
    finglish: str | None = None


class EditIn(BaseModel):
    text: str | None = None
    words: list[WordIn] = []


class SendIn(BaseModel):
    again: bool = False                 # voice the block's present text again (a new take), also when nothing was corrected


class ReplaceIn(BaseModel):
    find: str
    replace: str
    apply: bool = False                 # False: only show where the words stand


class NoteIn(BaseModel):
    note: str | None = None


def _version(db: Session, version_id: int) -> StoryVersion:
    version = db.get(StoryVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


def _review(db: Session, version: StoryVersion) -> VoiceReview:
    review = S.get_review(db, version)
    if review is None:
        raise HTTPException(status_code=409, detail="Open the review of this narration first")
    return review


def _guard(call):
    try:
        return call()
    except S.ReviewError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


def take_path(version: StoryVersion, take: VoiceBlockTake, block_id: str, suffix: str) -> Path:
    req = S.request_of(json.loads(take.request_json))
    key = VoiceRenderer.cache_key(req, ai_config.voice.elevenlabs.output_format)
    return S.narration_folder(version) / "blocks" / f"{block_id}__{key}{suffix}"


def take_voice(version: StoryVersion, take: VoiceBlockTake) -> dict:
    """What a take was voiced with: the provider's speed and stability, and the post speed the finished file gets."""
    settings = json.loads(take.request_json)["settings"]
    return {"speed": settings.get("speed"), "stability": settings.get("stability"), "post_speed": ai_config.voice.post_speed.get(version.language or "fa", 1.0)}


def block_view(db: Session, version: StoryVersion, block: VoiceBlock, route: str = "block-review") -> dict:
    text = block.draft_text if block.draft_text is not None else block.voice_text
    marked = S.words_of(db, block)
    takes = S.takes_of(db, block)
    return {"block_id": block.block_id, "position": block.position, "canonical_text": block.canonical_text, "voice_text": block.voice_text,
            "draft_text": block.draft_text, "text": text, "edited": block.edited, "needs_sending": S.needs_sending(db, block),
            "words": [{"n": w.n, "surface": w.surface, "corrected": w.n in marked, "was": marked[w.n].was if w.n in marked else "",
                       "finglish": marked[w.n].finglish if w.n in marked else "", "pending": marked[w.n].pending if w.n in marked else False}
                      for w in tokenize(text)],
            "takes": [{"take_no": t.take_no, "source": t.source, "active": t.active, "duration_s": t.duration_s, "cost_eur": round(t.cost_eur, 5),
                       "text": t.text_sent, "aliases": json.loads(t.aliases_json or "[]"), "created_at": t.created_at.isoformat(), "voice": take_voice(version, t),
                       "audio_url": f"/api/documentary/versions/{version.id}/{route}/blocks/{block.block_id}/takes/{t.take_no}/audio"}
                      for t in takes]}


def overview(db: Session, version: StoryVersion, review: VoiceReview) -> dict:
    blocks = S.blocks_of(db, review)
    approval = VRV.state(version)
    return {"exists": True, "version_id": version.id, "language": review.language, "status": review.status, "assembled_current": S.assembled_is_current(db, review),
            "assembled_at": review.assembled_at.isoformat() if review.assembled_at else None, "approved": approval["approved"],
            "corrected_blocks": sum(1 for b in blocks if b.edited), "to_send": sum(1 for b in blocks if S.needs_sending(db, b)),
            "assembled_audio_url": f"/api/documentary/versions/{version.id}/block-review/assembled-audio" if review.assembled_at else None,
            "blocks": [block_view(db, version, b) for b in blocks]}


@router.get("/api/documentary/versions/{version_id}/block-review")
def get_review(version_id: int, db: Session = Depends(get_db)):
    version = _version(db, version_id)
    review = S.get_review(db, version)
    if review is None:
        return {"exists": False, "version_id": version.id, "can_open": (S.narration_folder(version) / "manifest.json").exists()}
    return overview(db, version, review)


@router.post("/api/documentary/versions/{version_id}/block-review/open")
def open_review(version_id: int, db: Session = Depends(get_db)):
    version = _version(db, version_id)
    return overview(db, version, _guard(lambda: S.open_review(db, version)))


@router.put("/api/documentary/versions/{version_id}/block-review/blocks/{block_id}")
def save_edit(version_id: int, block_id: str, body: EditIn, db: Session = Depends(get_db)):
    version = _version(db, version_id)
    review = _review(db, version)
    try:
        block = _guard(lambda: S.save_edit(db, review, block_id, body.text, [E.WordEdit(w.n, w.surface, w.finglish) for w in body.words]))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return block_view(db, version, block)


@router.post("/api/documentary/versions/{version_id}/block-review/blocks/{block_id}/send")
async def send_block(version_id: int, block_id: str, body: SendIn | None = None, db: Session = Depends(get_db)):
    version = _version(db, version_id)
    review = _review(db, version)
    try:
        await S.send_block(db, review, block_id, again=bool(body and body.again))
    except S.ReviewError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return block_view(db, version, S.block_of(db, review, block_id))


@router.post("/api/documentary/versions/{version_id}/block-review/replace")
def replace_everywhere(version_id: int, body: ReplaceIn, db: Session = Depends(get_db)):
    """Words to find and the words for their place, in every block: the preview, or (apply) a correction of each block that has them."""
    version = _version(db, version_id)
    review = _review(db, version)
    matches = _guard(lambda: S.replace_everywhere(db, review, body.find, body.replace, body.apply))
    return {"applied": body.apply, "blocks_found": len(matches), "places_found": sum(m["count"] for m in matches), "matches": matches, **(overview(db, version, review) if body.apply else {})}


@router.post("/api/documentary/versions/{version_id}/block-review/send-all")
async def send_all(version_id: int, db: Session = Depends(get_db)):
    """Voices every corrected block that has not been sent yet, one after the other (a block that fails stays a draft and is reported)."""
    version = _version(db, version_id)
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


@router.post("/api/documentary/versions/{version_id}/block-review/blocks/{block_id}/takes/{take_no}/activate")
def activate(version_id: int, block_id: str, take_no: int, db: Session = Depends(get_db)):
    version = _version(db, version_id)
    review = _review(db, version)
    _guard(lambda: S.activate(db, review, block_id, take_no))
    return block_view(db, version, S.block_of(db, review, block_id))


@router.get("/api/documentary/versions/{version_id}/block-review/blocks/{block_id}/takes/{take_no}/audio")
def take_audio(version_id: int, block_id: str, take_no: int, db: Session = Depends(get_db)):
    version = _version(db, version_id)
    block = S.block_of(db, _review(db, version), block_id)
    take = next((t for t in S.takes_of(db, block) if t.take_no == take_no), None)
    path = take_path(version, take, block_id, ".wav") if take else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Take not found")
    return serve_file(path, "audio/wav")


@router.post("/api/documentary/versions/{version_id}/block-review/assemble")
async def assemble(version_id: int, db: Session = Depends(get_db)):
    version = _version(db, version_id)
    review = _review(db, version)
    try:
        made = await S.assemble(db, review)
    except S.ReviewError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {**made, **overview(db, version, review)}


@router.get("/api/documentary/versions/{version_id}/block-review/assembled-audio")
def assembled_audio(version_id: int, db: Session = Depends(get_db)):
    version = _version(db, version_id)
    path = S.reviewed_folder(version) / "narration.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Nothing assembled yet")
    return serve_file(path, "audio/mpeg")


@router.post("/api/documentary/versions/{version_id}/block-review/approve")
async def approve(version_id: int, body: NoteIn, db: Session = Depends(get_db)):
    """The one way to the film's narration: assembles it from the ticked takes when needed, makes it the film's and approves it (minutes, no new voice)."""
    version = _version(db, version_id)
    review = _review(db, version)
    try:
        await S.approve(db, review, body.note)
    except S.ReviewError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return overview(db, version, review)


def narration_state(db: Session, case_id: int, language: str) -> dict:
    """Is a narration of this case in this language ready for the hand review, and is it still waiting for the person's approval."""
    rows = (db.query(StoryVersion).filter(StoryVersion.case_id == case_id, StoryVersion.language == language, StoryVersion.kind == "spoken",
                                          StoryVersion.status != "archived").order_by(StoryVersion.id.desc()).all())
    for version in rows:
        if (S.narration_folder(version) / "manifest.json").exists():
            state = VRV.state(version)
            return {"ready": True, "version_id": version.id, "language": language, "approved": state["approved"], "blink": not state["approved"],
                    "review_open": S.get_review(db, version) is not None}
    return {"ready": False, "version_id": None, "language": language, "approved": False, "blink": False, "review_open": False}


@router.get("/api/cases/{case_id}/voice-review-status")
def voice_review_status(case_id: int, language: str | None = None, db: Session = Depends(get_db)):
    """For the shortcut on the Monitoring tab: is a narration of this case ready for the hand review, and is it still waiting for the person's approval.
    Without a language: the first review language whose narration waits for approval, else the first that has a narration."""
    if language is not None:
        return narration_state(db, case_id, language)
    states = [narration_state(db, case_id, lang) for lang in ai_config.voice.review_languages]
    return next((s for s in states if s["blink"]), next((s for s in states if s["ready"]), states[0]))
