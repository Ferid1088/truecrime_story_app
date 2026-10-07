"""Narration + music mix for one story version (shared by the API and
the documentary pipeline)."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.documentary.music import DocumentaryMixer
from app.documentary.voice_render import VoiceRenderer


async def render_documentary_audio(
    plan: dict, *, case_id: int, story_version_id: int, max_seconds=None,
    style=None, force_block_ids=None, with_music: bool = True,
    renderer: VoiceRenderer | None = None, mixer: DocumentaryMixer | None = None,
    db: Session | None = None,
) -> dict:
    """Narration (+ music mix when the plan is directed).

    The mix chooses tracks from the shared music library and records each
    placement (MusicUsage); it uses `db` when given, else its own session
    — so callers without a session (the job pipeline) keep working."""
    renderer = renderer or VoiceRenderer()
    manifest = await renderer.render(
        plan, case_id=case_id, story_version_id=story_version_id,
        max_seconds=max_seconds, style=style, force_block_ids=force_block_ids,
    )
    if with_music and plan.get("directed"):
        out = renderer.out_dir(case_id, plan["language"], story_version_id)
        extra = {"db": db} if db is not None else {}
        manifest["mix"] = await (mixer or DocumentaryMixer()).mix(manifest, plan, out, **extra)
        manifest["audio_notes"] = plan.get("audio_notes")
        (out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    return manifest
