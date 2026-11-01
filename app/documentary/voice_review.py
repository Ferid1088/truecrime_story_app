"""The owner hears the narration before the film goes on.

For the languages in `voice.review_languages` the run stops right after the narration is made (before the pictures are planned and the film
is rendered) and waits for the owner's approval of exactly that audio. The approval is kept next to the narration and names its checksum: a
narration made again needs a new approval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import StoryVersion
from app.documentary.pipeline.errors import AwaitingVoiceReview
from app.documentary.voice_render import VoiceRenderer
from app.utils import utc_now

AUDIO = {"narration": "narration.mp3", "documentary": "documentary.mp3"}      # the voice alone, and with the music (the host is spliced in later)


def required(language: str) -> bool:
    return language in ai_config.voice.review_languages


def folder(version: StoryVersion) -> Path:
    return VoiceRenderer.out_dir_for(version.case_id, version.language or "en", version.id)


def checksum(version: StoryVersion) -> str | None:
    path = folder(version) / AUDIO["narration"]
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def state(version: StoryVersion) -> dict:
    """What the owner needs to review: whether it is required, the audio to listen to, and whether this exact audio is approved."""
    sha = checksum(version)
    record = {}
    try:
        record = json.loads((folder(version) / "voice_review.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"required": required(version.language or "en"), "version_id": version.id, "language": version.language, "narration_sha256": sha,
            "audio": {name: f"/api/documentary/versions/{version.id}/audio/{name}" for name, file in AUDIO.items() if (folder(version) / file).exists()},
            "approved": bool(sha and record.get("narration_sha256") == sha), "approved_at": record.get("approved_at"), "note": record.get("note")}


def approve(version: StoryVersion, note: str | None = None) -> dict:
    sha = checksum(version)
    if sha is None:
        raise FileNotFoundError("the narration of this version is not made yet")
    (folder(version) / "voice_review.json").write_text(json.dumps(
        {"narration_sha256": sha, "approved_at": utc_now().isoformat(), "note": note}, ensure_ascii=False), encoding="utf-8")
    return state(version)


def guard(version: StoryVersion, language: str) -> None:
    """Stops the run (resumable) until the narration of a language the owner hears first is approved."""
    if not required(language):
        return
    review = state(version)
    if not review["approved"]:
        link = review["audio"].get("narration") or review["audio"].get("documentary") or ""
        raise AwaitingVoiceReview(
            f"Awaiting voice review: the {language} narration is made ({link}). Listen to it, approve it "
            f"(POST /api/documentary/versions/{version.id}/voice-review), then resume. Nothing else is planned or rendered before.")
