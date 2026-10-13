"""The recognisable host (Fereidoun): approved reference images per outfit.

The video's host is an avatar look (HeyGen); the thumbnail must show the
SAME outfit. Approved cut-outs (transparent PNG) are registered in the
host manifest, each tied to an `outfit_id` and, when known, the avatar
look ids that outfit corresponds to:

  {"assets": [{"id": "HOST_TC_03", "outfit_id": "OUTFIT_TC_03",
               "pose": "front_calm", "facing": "left|right|front",
               "file": "outfit03_front.png", "approved": true,
               "avatar_look_ids": ["<heygen look id>"]}]}

No approved host image -> no thumbnail (the composer never invents a host
or a different costume)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import AppSetting, HostScene
from app.outfits import OutfitRegistry

OUTFIT_ROOT = Path(__file__).resolve().parents[2] / "data" / "outfits"


class HostMissing(ValueError):
    pass


@dataclass(frozen=True)
class HostAsset:
    id: str
    outfit_id: str
    pose: str
    facing: str
    path: Path
    approved: bool
    avatar_look_ids: tuple[str, ...]


def load_assets() -> list[HostAsset]:
    out: list[HostAsset] = []
    for outfit in OutfitRegistry(OUTFIT_ROOT).all():
        out.extend(HostAsset(
            id=f"HOST_{outfit.outfit_id}_{pose.role}", outfit_id=outfit.outfit_id,
            pose=pose.role, facing="front", path=pose.path, approved=True,
            avatar_look_ids=outfit.heygen_look_ids,
        ) for pose in outfit.poses.values())
    return out


def outfit_for_episode(db: Session, case_id: int, language: str, identity=None) -> str:
    """The outfit of the video: the identity's stored outfit, else the one
    matching the avatar look the host scenes were rendered with, else the
    configured default. HostMissing when none can be determined."""
    registry = OutfitRegistry(OUTFIT_ROOT)
    setting = db.get(AppSetting, f"thumbnail:outfit:{case_id}:{language}")
    if setting is not None:
        try:
            selected = json.loads(setting.value_json or "{}").get("outfit_id")
            if selected:
                return registry.resolve(selected).outfit_id
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HostMissing(f"selected outfit is invalid for {language}") from exc
    if identity is not None and identity.host_outfit_id:
        try:
            return registry.resolve(identity.host_outfit_id).outfit_id
        except (KeyError, ValueError) as exc:
            raise HostMissing(f"outfit {identity.host_outfit_id!r} is not registered") from exc
    assets = load_assets()
    scenes = (db.query(HostScene).filter(HostScene.case_id == case_id,
                                         HostScene.language == language)
              .order_by(HostScene.id.desc()).all())
    for sc in scenes:
        look = getattr(sc, "avatar_id", None)
        for a in assets:
            if look and look in a.avatar_look_ids:
                return a.outfit_id
    if ai_config.thumbnail.default_outfit_id:
        try:
            return registry.resolve(ai_config.thumbnail.default_outfit_id).outfit_id
        except (KeyError, ValueError) as exc:
            raise HostMissing(f"outfit {ai_config.thumbnail.default_outfit_id!r} is not registered") from exc
    raise HostMissing("Choose a host outfit for this language before composing a thumbnail.")


def hosts_for_outfit(outfit_id: str) -> list[HostAsset]:
    try:
        canonical = OutfitRegistry(OUTFIT_ROOT).resolve(outfit_id).outfit_id
    except (KeyError, ValueError) as exc:
        raise HostMissing(f"outfit {outfit_id!r} is not registered") from exc
    found = [a for a in load_assets() if a.outfit_id == canonical and a.approved
             and a.path.exists()]
    if not found:
        raise HostMissing(f"This host outfit has no approved pose image yet: {outfit_id}.")
    return found


def pick_host(outfit_id: str, side: str, *, pose: str | None = None, seed: str = "") -> HostAsset:
    """The approved image of the outfit. The host faces the case picture
    (host on the left looks right, ...); an explicit pose wins; otherwise a
    stable per-episode choice among the fitting poses (variety without
    losing the brand)."""
    assets = hosts_for_outfit(outfit_id)
    if pose:
        for a in assets:
            if a.pose == pose or a.id == pose:
                return a
        raise HostMissing(f"pose {pose!r} is not approved for outfit {outfit_id}")
    want = "right" if side == "left" else "left"
    fitting = [a for a in assets if a.facing in (want, "front")] or assets
    idx = int(hashlib.sha1((seed + outfit_id).encode()).hexdigest(), 16) % len(fitting)
    return sorted(fitting, key=lambda a: a.id)[idx]
