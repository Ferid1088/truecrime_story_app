"""The owner's view of the short clips of a case: every language, every clip with its five parts, whether its voice is made and final, and its video when one was made."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.files import serve as serve_file
from app.db.base import get_db
from app.db.models import Case, ShortClip, VoiceReview
from app.documentary import opening_media as OM
from app.documentary import storage
from app.shortform import clips as C

router = APIRouter(tags=["short-clips"])

REVIEW_KIND = "clips"


def _voiced(script: dict, media: dict) -> bool:
    """Every part has a voice that was made from the part's current text."""
    voice = media.get("voice") or {}
    return all(voice.get(part) for part in C.TEXT_PARTS) and not OM.stale_voices({p: script.get(p, "") for p in C.TEXT_PARTS}, voice)


def _review_state(db: Session, row: ShortClip) -> str | None:
    review = (db.query(VoiceReview).filter_by(story_version_id=row.story_version_id, kind=REVIEW_KIND).first() if row.story_version_id else None)
    if review is None:
        return None
    return "finished" if review.status == "approved" else "in_review"


def _view(db: Session, row: ShortClip) -> dict:
    script = json.loads(row.script_json or "{}")
    media = json.loads(row.media_json or "{}")
    video = media.get("video") or None
    path = storage.resolve(video["path"]) if video and video.get("path") else None
    return {"clip_key": row.clip_key, "angle": script.get("angle"), "kind": row.kind, "status": row.status,
            "parts": {part: script.get(part, "") for part in C.TEXT_PARTS},
            "voiced": _voiced(script, media), "voice_seconds": media.get("voice_seconds"), "review": _review_state(db, row),
            "video": ({"seconds": video.get("seconds"), "bytes": video.get("bytes"),
                       "url": f"/api/cases/{row.case_id}/short-clips/{row.language}/{row.clip_key}/video"}
                      if path is not None and path.is_file() else None)}


@router.get("/api/cases/{case_id}/short-clips")
def short_clips(case_id: int, db: Session = Depends(get_db)):
    """The newest valid version of each clip, grouped by language."""
    if db.get(Case, case_id) is None:
        raise HTTPException(status_code=404, detail="Case not found")
    languages = sorted({language for (language,) in db.query(ShortClip.language).filter(ShortClip.case_id == case_id).distinct()})
    return {"languages": [{"language": language, "clips": [_view(db, row) for row in C.clips_of(db, case_id, language)]} for language in languages
                          if C.clips_of(db, case_id, language)]}


@router.get("/api/cases/{case_id}/short-clips/{language}/{clip_key}/video")
def short_clip_video(case_id: int, language: str, clip_key: str, db: Session = Depends(get_db)):
    row = next((r for r in C.clips_of(db, case_id, language) if r.clip_key == clip_key), None)
    video = (json.loads(row.media_json or "{}").get("video") if row else None) or {}
    return serve_file(storage.resolve(video.get("path")), "video/mp4", missing="This clip has no video yet")
