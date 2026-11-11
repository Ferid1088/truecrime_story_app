"""What is made from the short clips of a language: the voice of each part of each clip (the hook, the context, the detail, the reaction, the invitation), in the voice
of the opening block's hook before the host: the language's own style, the local speed made faster, the stability lower (opening_block.voice). One voice for each part,
so each text can be listened to and corrected on its own."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import ShortClip
from app.documentary import opening_media as OM
from app.documentary import storage
from app.documentary.voice_render import VoiceRenderer
from app.shortform import clips as C

PARTS = C.TEXT_PARTS


def clip_dir(case_id: int, language: str) -> Path:
    d = storage.case_dir(case_id) / "clips" / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def block_id(clip_key: str, part: str) -> str:
    return f"clip_{clip_key}_{part}"


async def voice_clip(db: Session, row: ShortClip, renderer: VoiceRenderer | None = None) -> dict:
    """The voice of every part of one clip; kept in the row's media_json. A part whose saved voice was made from the same text, voice and settings is reused."""
    script = json.loads(row.script_json)
    folder = clip_dir(row.case_id, row.language)
    voice = {}
    for part in PARTS:
        voice[part] = await OM.voice_part(db, row.case_id, row.language, part, script[part], renderer, folder=folder, block_id=block_id(row.clip_key, part))
    media = json.loads(row.media_json or "{}")
    media["voice"] = voice
    media["voice_seconds"] = round(sum(v["seconds"] for v in voice.values()), 3)
    row.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return media


async def voice_clips(db: Session, case_id: int, language: str, renderer: VoiceRenderer | None = None) -> list[dict]:
    """The voices of all the valid clips of a language, one clip after the other."""
    return [await voice_clip(db, row, renderer) for row in C.clips_of(db, case_id, language)]
