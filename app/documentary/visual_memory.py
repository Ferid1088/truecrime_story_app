"""Visual memory of the host (Part 7d): what he looked like in each past
appearance, so the stage director can avoid repeating a whole combination
without ever taking a worse editorial direction just to be different."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import HostAppearance, HostScene
from app.documentary import stage_direction as SD
from app.utils import utc_now


def direction_sha256(direction: SD.StageDirection) -> str:
    return hashlib.sha256(direction.model_dump_json().encode("utf-8")).hexdigest()


def combination(shots: list[dict], outfit_id: str, posture: str, lighting: str) -> tuple:
    return (tuple((s["camera"], s["framing"]) for s in shots), outfit_id, posture, lighting)


def appearance_dict(row: HostAppearance) -> dict:
    return {"id": row.id, "case_id": row.case_id, "host_scene_id": row.host_scene_id,
            "language": row.language, "channel": row.channel, "position": row.position,
            "studio_profile_id": row.studio_profile_id,
            "shots": json.loads(row.shots_json or "[]"), "outfit_id": row.outfit_id,
            "look_id": row.look_id, "posture": row.posture,
            "gestures": json.loads(row.gestures_json or "[]"), "lighting": row.lighting,
            "emotion": row.emotion, "interaction_pattern": row.interaction_pattern,
            "persona_sha256": row.persona_sha256, "direction_sha256": row.direction_sha256,
            "created_at": row.created_at}


def recent(db: Session, channel: str, limit: int | None = None) -> list[dict]:
    """The newest appearances of THIS channel, newest first."""
    n = limit or ai_config.stage_direction.visual_memory_appearances
    rows = (db.query(HostAppearance).filter(HostAppearance.channel == channel)
            .order_by(HostAppearance.created_at.desc(), HostAppearance.id.desc()).limit(n).all())
    return [appearance_dict(r) for r in rows]


def last_outfit_any_channel(db: Session) -> str | None:
    """The host is one person: the outfit of his last appearance counts across
    all channels."""
    row = db.query(HostAppearance).order_by(
        HostAppearance.created_at.desc(), HostAppearance.id.desc()).first()
    return row.outfit_id if row else None


def repeats_full_combination(direction: SD.StageDirection, past: list[dict]) -> bool:
    now = combination([s.model_dump() for s in direction.camera_plan], direction.outfit_id,
                      direction.posture, direction.lighting.variation)
    return any(combination(p["shots"], p["outfit_id"], p["posture"], p["lighting"]) == now
               for p in past)


def record(db: Session, scene: HostScene, direction: SD.StageDirection,
           errors: list[dict], look_id: str | None = None,
           persona_sha256: str | None = None) -> HostAppearance:
    """Store the accepted direction on the scene and its appearance row, in
    one transaction (the caller commits). A direction with errors is never
    stored."""
    if errors:
        raise ValueError(f"a rejected direction is not remembered: {errors}")
    existing = db.query(HostAppearance).filter(HostAppearance.host_scene_id == scene.id).first()
    if existing is not None and scene.direction_json:
        raise ValueError("this scene's appearance is already recorded")
    sha = direction_sha256(direction)
    scene.direction_json = direction.model_dump_json()
    scene.direction_sha256 = sha
    gestures = [a.action for a in direction.performance_timeline if a.action in SD.GESTURES]
    fields = dict(
        case_id=scene.case_id, host_scene_id=scene.id, language=scene.language,
        channel=scene.channel, position=scene.position, studio_profile_id=direction.studio_id,
        shots_json=json.dumps([{"camera": s.camera, "framing": s.framing, "movement": s.movement,
                                "start_sec": s.start_sec, "end_sec": s.end_sec}
                               for s in direction.camera_plan]),
        outfit_id=direction.outfit_id, look_id=look_id, posture=direction.posture,
        gestures_json=json.dumps(gestures), lighting=direction.lighting.variation,
        emotion=direction.emotion.primary, interaction_pattern=direction.purpose,
        persona_sha256=persona_sha256, direction_sha256=sha, created_at=utc_now())
    if existing is not None:      # a redone scene: the memory keeps its newest direction
        for key, value in fields.items():
            setattr(existing, key, value)
        row = existing
    else:
        row = HostAppearance(**fields)
        db.add(row)
    db.flush()
    return row
