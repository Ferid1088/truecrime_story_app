"""Stage direction of a host appearance (Part 7c): the director file's JSON
shape as a validated model, and the deterministic guard-rails that hold it to
the approved looks, the audio's own timing and the sensitive-material rules.

The audio is the source of truth: nothing here changes wording, pauses or
timing; the direction only adapts to voice_seconds and the sentence ends of the
stored character alignment."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.ai_config import ai_config

CAMERAS = ("CAM01_FRONT", "CAM02_LEFT_45", "CAM03_RIGHT_45", "CAM04_LEFT_SIDE",
           "CAM05_RIGHT_SIDE", "CAM06_WIDE")
FRAMINGS = ("WIDE", "MEDIUM", "MEDIUM_CLOSE", "CLOSE")
MOVEMENTS = ("STATIC", "SUBTLE_PUSH_IN", "SUBTLE_PULL_BACK")
POSTURES = ("P01_NEUTRAL_SEATED", "P02_SLIGHT_FORWARD", "P03_RELAXED_BACK",
            "P04_ARMREST_RELAXED", "P05_HANDS_LOOSELY_JOINED")
GESTURES = ("G01_NONE", "G02_SMALL_OPEN_PALM", "G03_SUBTLE_TWO_HAND_EXPLANATION",
            "G04_SMALL_HEAD_TILT", "G05_THOUGHTFUL_PAUSE", "G06_SLIGHT_NOD",
            "G07_SHORT_LOOK_AWAY_RETURN")
EMOTIONS = ("NEUTRAL", "CURIOUS", "ANALYTICAL", "CONCERNED", "SKEPTICAL", "REFLECTIVE",
            "AFFECTED", "CAUTIOUS", "SURPRISED", "SHOCKED", "TERRIFIED", "OUTRAGED")
LIGHTING = ("L01_BRAND_DEFAULT", "L02_WARM_PERSONAL", "L03_ANALYTICAL_NEUTRAL",
            "L04_REFLECTIVE_SOFT", "L05_SLIGHT_HIGH_CONTRAST", "L06_VICTIM_SENSITIVE_SOFT")
EYE_CONTACT = ("DIRECT", "MOSTLY_DIRECT", "OCCASIONAL_LOOK_AWAY")
SENSITIVE_LIGHTING = "L06_VICTIM_SENSITIVE_SOFT"


class Emotion(BaseModel):
    primary: str
    intensity: float = Field(ge=0.0, le=1.0)
    justification: str | None = None   # required for a forbidden emotion


class Shot(BaseModel):
    start_sec: float
    end_sec: float
    camera: str
    framing: str
    movement: str


class Action(BaseModel):
    start_sec: float
    end_sec: float
    action: str


class Lighting(BaseModel):
    base: str = "STUDIO_DEFAULT"
    variation: str


class StageDirection(BaseModel):
    appearance_id: str
    purpose: str
    mode: str = "direct_to_viewer"
    studio_id: str
    emotion: Emotion
    camera_plan: list[Shot] = Field(min_length=1)
    outfit_id: str
    posture: str
    eye_contact: str
    performance_timeline: list[Action] = Field(min_length=1)
    lighting: Lighting
    memory_reference: str | None = None
    continuity_reason: str | None = None   # lets an outfit repeat
    avoid: list[str] = []
    director_reasoning_summary: str = ""


def _covers(items: list, total: float, name: str, errors: list[dict]) -> None:
    """The timeline runs from 0 to the voice's end with no gap and no overlap."""
    cursor = 0.0
    for it in sorted(items, key=lambda x: x.start_sec):
        if abs(it.start_sec - cursor) > 0.05:
            errors.append({"code": f"{name}_gap_or_overlap", "at": it.start_sec, "expected": cursor})
        cursor = it.end_sec
    if abs(cursor - total) > 0.05:
        errors.append({"code": f"{name}_does_not_cover_the_voice", "ends": cursor, "voice": total})


def validate_direction(direction: StageDirection, *, voice_seconds: float,
                       sentence_ends: list[float], approved_looks: set[tuple[str, str]],
                       recent: list[dict], sensitive: bool, memory_refs: set[str],
                       outfit_ids: set[str]) -> list[dict]:
    """approved_looks: {(camera, framing)}; recent: newest-first appearances of
    the same channel ({outfit_id, posture}); returns the errors."""
    cfg = ai_config.stage_direction
    errors: list[dict] = []
    plan = sorted(direction.camera_plan, key=lambda s: s.start_sec)
    for s in plan:
        if s.camera not in CAMERAS:
            errors.append({"code": "unknown_camera", "camera": s.camera})
        elif (s.camera, s.framing) not in approved_looks:
            errors.append({"code": "look_not_approved", "camera": s.camera, "framing": s.framing})
        if s.framing not in FRAMINGS or s.movement not in MOVEMENTS:
            errors.append({"code": "unknown_framing_or_movement", "framing": s.framing,
                           "movement": s.movement})
    _covers(plan, voice_seconds, "camera_plan", errors)
    _covers(direction.performance_timeline, voice_seconds, "performance_timeline", errors)
    for i, s in enumerate(plan):
        length = s.end_sec - s.start_sec
        if len(plan) > 1 and not cfg.min_shot_seconds <= length <= cfg.max_shot_seconds:
            errors.append({"code": "shot_length", "shot": i, "seconds": round(length, 2)})
        if i and not any(abs(s.start_sec - e) <= cfg.cut_alignment_tolerance_seconds
                         for e in sentence_ends):
            errors.append({"code": "cut_not_at_a_sentence_end", "at": s.start_sec})
    closes = [s for s in plan if s.framing == "CLOSE"]
    if len(closes) > cfg.max_close_shots_per_appearance:
        errors.append({"code": "too_many_close_shots", "count": len(closes)})
    if closes and not (direction.emotion.primary.lower() in cfg.close_allowed_emotions
                       or direction.purpose in cfg.close_allowed_purposes):
        errors.append({"code": "close_without_reason"})
    if (direction.emotion.primary.upper() in cfg.forbidden_emotions
            and not (direction.emotion.justification or "").strip()):
        errors.append({"code": "forbidden_emotion", "emotion": direction.emotion.primary})
    if direction.outfit_id not in outfit_ids:
        errors.append({"code": "unknown_outfit", "outfit": direction.outfit_id})
    window = [r["outfit_id"] for r in recent[:cfg.recent_outfit_window]]
    if direction.outfit_id in window and not (direction.continuity_reason or "").strip():
        errors.append({"code": "outfit_repeated", "outfit": direction.outfit_id})
    if recent and recent[0].get("posture") == direction.posture:
        errors.append({"code": "posture_same_as_previous", "posture": direction.posture})
    if direction.posture not in POSTURES or direction.lighting.variation not in LIGHTING:
        errors.append({"code": "unknown_posture_or_lighting"})
    if direction.memory_reference and direction.memory_reference not in memory_refs:
        errors.append({"code": "unknown_memory_reference", "ref": direction.memory_reference})
    if sensitive:
        gestures = [a for a in direction.performance_timeline
                    if a.action in GESTURES and a.action != "G01_NONE"]
        if direction.lighting.variation != SENSITIVE_LIGHTING:
            errors.append({"code": "sensitive_needs_soft_lighting"})
        if len(gestures) > cfg.sensitive_max_gestures:
            errors.append({"code": "sensitive_too_many_gestures", "count": len(gestures)})
        if any(s.movement != "STATIC" for s in plan):
            errors.append({"code": "sensitive_needs_static_camera"})
    return errors


def sentence_ends(alignment: dict) -> list[float]:
    """End time of every sentence in a stored character alignment
    ({"characters": [...], "ends": [...]})."""
    chars, ends = alignment.get("characters") or [], alignment.get("ends") or []
    return [round(float(e), 3) for c, e in zip(chars, ends) if c in ".!?؟…"]


# What actually executes each direction field (docs/host-direction.md). A
# field whose renderer or provider part is not built shows as PLANNED_ONLY.
EXECUTION = {
    "studio_id": ("EXECUTED_BY_LOOK_CHOICE", True),
    "camera": ("EXECUTED_BY_LOOK_CHOICE", True),
    "framing": ("EXECUTED_IN_OUR_RENDERER", False),
    "movement": ("EXECUTED_IN_OUR_RENDERER", False),
    "outfit_id": ("EXECUTED_BY_LOOK_CHOICE", True),
    "posture": ("EXECUTED_BY_PROVIDER", True),
    "gestures": ("EXECUTED_BY_PROVIDER", True),
    "eye_contact": ("EXECUTED_BY_PROVIDER", True),
    "emotion_intensity": ("EXECUTED_BY_PROVIDER", True),
    "emotion": ("PLANNED_ONLY", False),
    "performance_timeline": ("PLANNED_ONLY", False),
    "lighting": ("EXECUTED_IN_OUR_RENDERER", False),
}


def execution_view(direction: dict | None) -> list[dict]:
    """The stored direction field by field with how each is (or is not) executed."""
    if not direction:
        return []
    shots = direction.get("camera_plan") or []
    values = {
        "studio_id": direction.get("studio_id"),
        "camera": ", ".join(dict.fromkeys(s["camera"] for s in shots)),
        "framing": ", ".join(dict.fromkeys(s["framing"] for s in shots)),
        "movement": ", ".join(dict.fromkeys(s["movement"] for s in shots)),
        "outfit_id": direction.get("outfit_id"), "posture": direction.get("posture"),
        "gestures": ", ".join(a["action"] for a in direction.get("performance_timeline") or []
                              if a.get("action") in GESTURES and a["action"] != "G01_NONE") or "none",
        "eye_contact": direction.get("eye_contact"),
        "emotion_intensity": (direction.get("emotion") or {}).get("intensity"),
        "emotion": (direction.get("emotion") or {}).get("primary"),
        "performance_timeline": f"{len(direction.get('performance_timeline') or [])} actions",
        "lighting": (direction.get("lighting") or {}).get("variation"),
    }
    out = []
    for field, value in values.items():
        target, implemented = EXECUTION[field]
        out.append({"field": field, "value": value, "target": target,
                    "executed": implemented, "shown_as": target if implemented else "PLANNED_ONLY"})
    return out
