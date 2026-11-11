"""The pieces of a cover: the folder of a language's covers, the avatar of the film's outfit and the drawing of one cover from its proposal.

The avatar is the host of the film's outfit, the scene is the picture the image model made for the cover, the title is the cover's (1 to 4 words, in the language)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers import avatar as AV
from app.covers import compose as C
from app.db.models import Case, CoverImage, CoverProposal
from app.documentary import outfits as OF
from app.documentary import storage
from app.thumbnails import hosts as H


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


def film_avatar(db: Session, case_id: int, language: str) -> tuple[str, Path]:
    """The outfit the language's film wears (the one selected for it) and the image of the avatar in it."""
    chosen = OF.selected_outfit(db, case_id, language)
    if chosen is None:
        raise AV.AvatarError(f"no outfit is selected for the {language} film: choose the avatar's outfit first")
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
