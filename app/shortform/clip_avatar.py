"""The avatar of a short clip: the film's own look speaks the clip's voice (HeyGen), one video for the hook and one for the reaction and the invitation.

The look is the one the language's film used (the outfit of its host scenes), the closest framing first (config short_clips.video.avatar_framing), set in the
film's studio. Each video is booked in the ledger as an estimate by its seconds, once per job."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import HostLook, HostScene
from app.documentary import outfits as OF
from app.documentary import storage
from app.documentary import studio as ST
from app.documentary.heygen_cost import book_job
from app.providers.avatar.base import AvatarProvider, AvatarProviderError
from app.services.budget import BudgetService


class NoFilmLook(RuntimeError):
    pass


def film_look(db: Session, case_id: int, language: str) -> tuple[str, str]:
    """(HeyGen look id, studio asset id) of the language's film: the outfit of its first host scene with a video, in the closest framing that was uploaded and approved."""
    scene = (db.query(HostScene).filter(HostScene.case_id == case_id, HostScene.language == language, HostScene.avatar_video_path.isnot(None)).order_by(HostScene.id).first())
    outfit = OF.scene_outfit(db, scene) if scene is not None else None
    if scene is None or outfit is None:
        raise NoFilmLook(f"the {language} film has no host scene with a video: its look is unknown")
    looks = db.query(HostLook).filter(HostLook.host_outfit_id == outfit.id, HostLook.status == "approved", HostLook.heygen_look_id.isnot(None)).all()
    for framing in ai_config.short_clips_settings().video.avatar_framing:
        for look in looks:
            if look.framing == framing:
                return look.heygen_look_id, scene.studio_asset_id
    raise NoFilmLook(f"the outfit {outfit.name} has no uploaded look in the framings {ai_config.short_clips_settings().video.avatar_framing}")


async def make_video(db: Session, case_id: int, language: str, audio: Path, name: str, folder: Path, provider: AvatarProvider, *, sleep=asyncio.sleep) -> dict:
    """One avatar video from an audio file: uploaded, requested, waited for, downloaded and booked. Returns {path, seconds, job_id}. Stops before the paid call when
    the case's budget is spent."""
    cfg = ai_config.avatar
    look_id, studio_asset = film_look(db, case_id, language)
    BudgetService(db).check(case_id, "heygen:avatar")
    registry = ST.load_registry()
    asset = registry.asset(studio_asset)
    if asset is None or asset.language != language:
        raise AvatarProviderError("invalid_request", f"studio {studio_asset} is not a {language} asset")
    audio_id = await provider.upload_asset(audio, "audio/mpeg")
    background_id = await provider.upload_asset(ST.asset_path(registry, asset), "image/png")
    key = f"tc-clip-{case_id}-{name}-{hashlib.sha256(audio.read_bytes()).hexdigest()[:12]}"
    job = await provider.create_video(look_id, audio_id, key, output_format="mp4", resolution=cfg.resolution, background_asset_id=background_id, title=f"clip {case_id} {name}",
                                      **({"engine": cfg.engine} if cfg.engine else {}), **({"fit": cfg.fit} if cfg.fit else {}))
    waited = 0.0
    while True:
        job = await provider.get_video(job.job_id)
        if job.status == "completed" and job.video_url:
            break
        if job.status == "failed":
            raise AvatarProviderError("job_failed", job.failure_message or "the avatar job failed")
        if waited >= cfg.max_poll_minutes * 60:
            raise AvatarProviderError("timeout", f"job {job.job_id} is still {job.status}")
        await sleep(cfg.poll_interval_s)
        waited += cfg.poll_interval_s
    dest = folder / f"avatar_{name}_{job.job_id}.mp4"
    await provider.download(job.video_url, dest)
    from app.documentary import audio as A

    seconds = float(job.duration) if job.duration else A.probe_duration(dest)
    book_job(db, case_id, job.job_id, seconds, language, f"short clip {name}")
    return {"path": storage.rel(dest), "seconds": round(seconds, 3), "job_id": job.job_id, "look_id": look_id}
