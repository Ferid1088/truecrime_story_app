"""The pieces of a cover: the folder of a language's covers, the avatar of the film's outfit, the drawing of one cover from its proposal, and its approval (by the owner, or by default
for the planner's own proposals: `covers.proposal.auto_approve`).

The avatar is the host of the film's outfit, the scene is the picture the image model made for the cover, the title is the cover's (1 to 4 words, in the language)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers import avatar as AV
from app.covers import compose as C
from app.covers import proposals as P
from app.db.models import Case, CoverImage, CoverProposal, HostScene
from app.documentary import outfits as OF
from app.documentary import storage
from app.thumbnails import hosts as H

log = logging.getLogger(__name__)


def covers_dir(case_id: int, language: str) -> Path:
    d = storage.case_dir(case_id) / "covers" / language
    d.mkdir(parents=True, exist_ok=True)
    return d


def standing_pose(outfit: str) -> H.HostAsset:
    """The first of the configured standing poses the outfit has."""
    have = {a.pose: a for a in H.hosts_for_outfit(outfit)}
    for name in ai_config.covers_settings().avatar["poses"]:
        if name in have:
            return have[name]
    raise AV.AvatarError(f"the outfit {outfit} has none of the standing poses {ai_config.covers_settings().avatar['poses']}")


def film_outfit(db: Session, case_id: int, language: str):
    """The outfit the language's film wears: the one of its first host scene that has a video (the outfit can differ from the one marked as selected), else the selected one."""
    scene = (db.query(HostScene).filter(HostScene.case_id == case_id, HostScene.language == language, HostScene.avatar_video_path.isnot(None))
             .order_by(HostScene.id).first())
    outfit = OF.scene_outfit(db, scene) if scene is not None else None
    return outfit or OF.selected_outfit(db, case_id, language)


def film_avatar(db: Session, case_id: int, language: str) -> tuple[str, Path]:
    """The outfit the language's film wears and the image of the avatar in it."""
    chosen = film_outfit(db, case_id, language)
    if chosen is None:
        raise AV.AvatarError(f"the {language} film has no outfit: choose the avatar's outfit first")
    if chosen.outfit_ref:
        return chosen.outfit_ref, standing_pose(chosen.outfit_ref).path
    if chosen.reference_image_path:
        return chosen.name, storage.resolve(chosen.reference_image_path)
    raise AV.AvatarError(f"the outfit {chosen.name} has no image")


def portrait_for(kind: str, avatar_image: Path):
    """The avatar's portrait cut to the shape of the cover's avatar box."""
    cfg = ai_config.covers_settings()
    box, size = cfg.layout[kind]["avatar"], cfg.formats[kind]
    return AV.portrait(avatar_image, box["width"] * size.width / (box["height"] * size.height), box["face_widths"])


def render_cover(db: Session, case: Case, language: str, row: CoverProposal, out: Path, *, scale: float) -> dict:
    """One cover drawn from a proposal row: its title over the scene picture made for it."""
    if not row.title or row.image_id is None:
        raise C.CoverError("the cover needs a title and its scene picture first")
    image = db.get(CoverImage, row.image_id)
    if image is None or not image.file_path:
        raise C.CoverError("the scene picture of this cover is missing")
    _outfit, avatar_image = film_avatar(db, case.id, language)
    return C.compose(row.kind, language, storage.resolve(image.file_path), portrait_for(row.kind, avatar_image), storage.ROOT / ai_config.channels[language].logo, row.title, out, scale=scale)


def preview_path(case_id: int, language: str, row: CoverProposal) -> Path:
    return covers_dir(case_id, language) / f"preview_{row.cover_key}_{row.id}.jpg"


def final_path(case_id: int, language: str, key: str) -> Path:
    return covers_dir(case_id, language) / ("long.jpg" if key == "long" else f"short_{key}.jpg")


def make_preview(db: Session, case: Case, language: str, row: CoverProposal) -> dict:
    return render_cover(db, case, language, row, preview_path(case.id, language, row), scale=ai_config.covers_settings().proposal["preview_scale"])


def approve_cover(db: Session, case: Case, language: str, row: CoverProposal) -> dict:
    """Approves the cover as it is, made in its full size. Raises C.CoverError when it cannot be approved (no title or picture, no preview of this very version, or a problem in the drawing)."""
    if not row.title or row.image_id is None:
        raise C.CoverError("A cover needs a title and its scene picture before it can be approved")
    if not preview_path(case.id, language, row).exists():
        raise C.CoverError("Look at the preview of this title and scene first")
    out = final_path(case.id, language, row.cover_key)
    made = render_cover(db, case, language, row, out, scale=1.0)
    if made["problems"]:
        raise C.CoverError("; ".join(made["problems"]))
    row.status = "approved"
    row.validation_json = json.dumps({**json.loads(row.validation_json or "{}"), "file": storage.rel(out), "size": made["size"], "bytes": made["bytes"]})
    db.commit()
    return made


def auto_approve(db: Session, case: Case, language: str) -> list[str]:
    """The covers the planner made (source `agent`, never one the owner wrote or changed) that already have a title and their scene picture and no problem found in them are approved
    by default, also those the planner was only moderately sure of (status `open` for its low confidence alone): the preview is made and the cover is drawn in full size, exactly as an
    approval by the owner does it (code only, nothing paid). A cover that cannot be drawn cleanly stays as it is. The owner can still change any of them; a changed cover is the owner's
    and waits for the owner's approval. Returns the keys approved now."""
    if not ai_config.covers_settings().proposal.get("auto_approve"):
        return []
    approved = []
    for key, row in P.current(db, case.id, language).items():
        if row.status not in ("proposed", "open") or row.source != "agent" or not row.title or row.image_id is None or json.loads(row.validation_json or "{}").get("issues"):
            continue
        try:
            make_preview(db, case, language, row)
            approve_cover(db, case, language, row)
        except (C.CoverError, AV.AvatarError) as exc:
            log.warning("cover %s of case %s (%s) stays proposed: %s", key, case.id, language, exc)
            continue
        approved.append(key)
    return approved
