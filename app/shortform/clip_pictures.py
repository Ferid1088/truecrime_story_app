"""The pictures of a clip's middle: mystery pictures the image model paints from scene descriptions that follow the clip's words (no film photo, no person, no victim, no
weapon), made once for a clip and shown in every language. The scenes are written for the English clip and kept in its script; every language's clip of the same idea
(its native row points to the English one) shows the same pictures, so a picture is never paid for twice."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers import images as IM
from app.db.models import Case, ShortClip
from app.documentary import storage


def master_of(db: Session, row: ShortClip) -> ShortClip:
    """The English clip a row's pictures belong to (the row itself when it is the English one)."""
    master = row if row.kind == "master" else db.get(ShortClip, row.master_clip_id) if row.master_clip_id else None
    if master is None:
        raise ValueError(f"clip {row.clip_key} ({row.language}) has no English clip its pictures belong to")
    return master


async def ensure_pictures(db: Session, case: Case, row: ShortClip, planner=None, provider=None) -> list[dict]:
    """The clip's pictures [{scene, path, image_id}]: the ones that exist, else the scenes (written once) painted. Stops before a call when the budget is spent."""
    cfg = ai_config.short_clips_settings().video.scenes
    master = master_of(db, row)
    scenes = json.loads(master.script_json).get("scenes")
    if not scenes:
        from app.shortform.clip_agents import ClipPlanner

        scenes = await (planner or ClipPlanner()).write_scenes(db, case, master)
    media = json.loads(master.media_json or "{}")
    known = {p["scene"]: p for p in media.get("pictures") or [] if storage.resolve(p["path"]).exists()}
    folder = storage.case_dir(case.id) / "clips" / "pictures"
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for n, scene in enumerate(scenes[:cfg.count]):
        if scene in known:
            made.append(known[scene])
            continue
        prompt = f"{scene.strip().rstrip('.')}. {cfg.style}"
        record = await IM.paint(db, case, "en", f"clip_{master.clip_key}_{n + 1}", master.id, prompt, cfg.size, folder, f"{master.clip_key}_{n + 1}", cfg.ledger_stage, provider)
        made.append({"scene": scene, "path": record.file_path, "image_id": record.id})
        media["pictures"] = made + [p for p in known.values() if p["scene"] not in {m["scene"] for m in made}]
        master.media_json = json.dumps(media, ensure_ascii=False)
        db.commit()
    media["pictures"] = made
    master.media_json = json.dumps(media, ensure_ascii=False)
    db.commit()
    return made
