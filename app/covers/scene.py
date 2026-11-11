"""The scene of a cover: a real picture the finished film shows.

Only pictures the language's production script puts on screen that the film may use (verified for the case, rights allowed and, for a news photo, approved
by a person) and that are no spoiler and show nothing graphic or from custody (the gates of the thumbnails). No face of a victim, a suspect or any person is used as a cover scene (config covers.scene.forbidden_kinds). A cover never
shows evidence the app made up; the configurable fallback image model (covers.fallback_image) is off until it is verified."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, ProductionScript, VisualAsset
from app.documentary import storage
from app.documentary.visuals import rights as R
from app.documentary.visuals.spoilers import revealed_by, shows_custody
from app.thumbnails import selection as S


class SceneMissing(RuntimeError):
    pass


def shown_codes(db: Session, case_id: int, language: str) -> list[str]:
    """The asset codes of the pictures the language's newest production script shows, in the order they first appear."""
    row = (db.query(ProductionScript).filter(ProductionScript.case_id == case_id, ProductionScript.language == language)
           .order_by(ProductionScript.id.desc()).first())
    if row is None:
        return []
    seen: list[str] = []
    for shot in json.loads(row.script_json or "{}").get("shots", []):
        code = shot.get("asset_id")
        if shot.get("kind") == "image" and code and code not in seen:
            seen.append(code)
    return seen


def reject_reason(asset, kind: str, late: set[str]) -> str | None:
    """Why a picture the film shows is no cover scene (None when it can be one)."""
    cfg = ai_config.covers_settings().scene
    if asset.verification_status != "verified":
        return "not verified for this case"
    if asset.asset_type not in S.IMAGE_TYPES or asset.provider == "generated" or asset.found_during == "generated":
        return "not a real still picture"
    if not R.usable(asset):
        return f"rights: {asset.rights_status} (not cleared for the film)"
    if kind in cfg["forbidden_kinds"]:
        return f"a {kind} is never a cover scene"
    if (asset.width or 0) < cfg["min_px"][0] or (asset.height or 0) < cfg["min_px"][1]:
        return f"resolution too low ({asset.width}x{asset.height})"
    if revealed_by(asset) & late:
        return "shows something the story reveals later (spoiler)"
    if shows_custody(asset):
        return "shows custody or court (spoils the outcome)"
    verification = json.loads(asset.verification_json or "{}")
    seen = " ".join(str(x or "") for x in (asset.description, asset.caption, asset.subject_type, verification.get("depicts"), verification.get("subject_type"))).lower() + " "
    if asset.entity_type == "person" or any(t in seen for t in cfg["person_terms"]):
        return "shows a person (no face is a cover scene)"
    if any(t in seen for t in ai_config.thumbnail.graphic_terms):
        return "graphic content"
    return None


def candidates(db: Session, case: Case, language: str) -> list[dict]:
    """The usable scene pictures: {code, kind, path, width, height}, best first (by what they show, then in the order the film shows them)."""
    shown = shown_codes(db, case.id, language)
    late = S.late_reveal_ids(db, case.id)
    usable = []
    for asset in db.query(VisualAsset).filter(VisualAsset.case_id == case.id, VisualAsset.asset_code.in_(shown or [""])).all():
        kind = S.asset_kind(asset)
        if reject_reason(asset, kind, late) is None and asset.local_path and storage.resolve(asset.local_path).exists():
            usable.append({"code": asset.asset_code, "kind": kind, "path": storage.resolve(asset.local_path), "width": asset.width, "height": asset.height,
                           "order": shown.index(asset.asset_code)})
    return sorted(usable, key=lambda c: (ai_config.thumbnail.subject_priority.get(c["kind"], 9), c["order"]))


def pick(options: list[dict], count: int) -> list[dict]:
    """`count` scenes, each picture used once while there are enough of them (then the best ones again); SceneMissing when there are none."""
    if not options:
        fallback = ai_config.covers_settings().fallback_image
        raise SceneMissing("no picture of the film can be a cover scene" + ("" if fallback.get("enabled") else "; the fallback image model is off"))
    return [{**options[i % len(options)], "reused": i >= len(options)} for i in range(count)]
