"""The host's outfit of one video (one case, one language) and the standard set of looks it is
given.

One outfit is selected per case and language and stays the same in every host scene of that
video. Its looks (camera, framing, pose) are generated, approved by the owner and uploaded as
before; the stage director only ever chooses among the approved looks of the selected outfit, in
the studio of the outfit's own language."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import HostLook, HostOutfit
from app.documentary import storage
from app.documentary import studio as ST
from app.outfits.registry import OutfitRegistry
from app.utils import utc_now

STANCES = ("SEATED", "STANDING")


class OutfitError(ValueError):
    pass


def create_outfit(db: Session, case_id: int, language: str, name: str,
                  outfit_ref: str | None = None, reference_image: Path | None = None) -> HostOutfit:
    """A new outfit (draft) for a case and language: an outfit of the app's registry, or an own
    reference picture."""
    if language not in ai_config.channels:
        raise OutfitError(f"no channel for language {language!r}")
    if not name.strip():
        raise OutfitError("an outfit needs a name")
    if not outfit_ref and reference_image is None:
        raise OutfitError("give an outfit of the registry or a reference picture")
    if outfit_ref:
        try:
            OutfitRegistry().resolve(outfit_ref)
        except KeyError as e:
            raise OutfitError(f"unknown outfit {outfit_ref!r}") from e
    path = sha = None
    if reference_image is not None:
        data = Path(reference_image).read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise OutfitError("the reference picture must be a PNG")
        sha = hashlib.sha256(data).hexdigest()
        path = str(Path(reference_image).resolve().relative_to(storage.ROOT)) \
            if str(Path(reference_image).resolve()).startswith(str(storage.ROOT)) \
            else str(Path(reference_image).resolve())
    outfit = HostOutfit(case_id=case_id, language=language, name=name.strip(), outfit_ref=outfit_ref,
                        reference_image_path=path, reference_image_sha256=sha)
    db.add(outfit)
    db.commit()
    db.refresh(outfit)
    return outfit


def create_outfit_from_picture(db: Session, case_id: int, language: str, name: str,
                               png: bytes) -> HostOutfit:
    """An outfit whose reference is the owner's own picture (kept under data/outfit_uploads)."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise OutfitError("the reference picture must be a PNG")
    path = storage.ROOT / "data" / "outfit_uploads" / f"case_{case_id}" / (
        hashlib.sha256(png).hexdigest()[:16] + ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return create_outfit(db, case_id, language, name, reference_image=path)


def select_outfit(db: Session, outfit: HostOutfit) -> HostOutfit:
    """The outfit of this case and language: any other selected outfit of the same video goes back
    to draft."""
    if outfit.status == "retired":
        raise OutfitError("a retired outfit cannot be selected")
    for other in db.query(HostOutfit).filter(
            HostOutfit.case_id == outfit.case_id, HostOutfit.language == outfit.language,
            HostOutfit.status == "selected", HostOutfit.id != outfit.id):
        other.status = "draft"
    outfit.status, outfit.selected_at = "selected", utc_now()
    db.commit()
    return outfit


def retire_outfit(db: Session, outfit: HostOutfit) -> HostOutfit:
    outfit.status = "retired"
    db.commit()
    return outfit


def selected_outfit(db: Session, case_id: int, language: str) -> HostOutfit | None:
    return (db.query(HostOutfit)
            .filter(HostOutfit.case_id == case_id, HostOutfit.language == language,
                    HostOutfit.status == "selected").first())


# ---------------------------------------------------------------------------
# the Outfits panel (O2): one outfit per language, optionally one fixed picture
# ---------------------------------------------------------------------------


def _language_outfits(db: Session, case_id: int, language: str) -> list[HostOutfit]:
    return (db.query(HostOutfit)
            .filter(HostOutfit.case_id == case_id, HostOutfit.language == language,
                    HostOutfit.status != "retired").order_by(HostOutfit.id).all())


def _same_named(db: Session, case_id: int, language: str, name: str) -> HostOutfit | None:
    """The outfit of another language with the same name (case-insensitive): the one
    "same outfit for all languages" selects there."""
    return next((o for o in _language_outfits(db, case_id, language)
                 if o.name.strip().lower() == name.strip().lower()), None)


def _final_rendered(db: Session, case_id: int, language: str) -> bool:
    """A full film of this language was rendered (or published): the owner may still change the
    outfit, but nothing changes on screen without a new render."""
    from app.db.models import Video

    return db.query(Video).filter(Video.case_id == case_id, Video.language == language,
                                  Video.mode == "full",
                                  Video.state.in_(["rendered", "published"])).count() > 0


def needs_outfit_choice(db: Session, case_id: int, language: str) -> bool:
    """No outfit is chosen for this case and language: drafts still work, but the final
    version must not — the autopilot lists it as "needs you: choose outfit"."""
    return selected_outfit(db, case_id, language) is None


def set_fixed_look(db: Session, outfit: HostOutfit, look_id: int | None) -> HostOutfit:
    """Checkbox C: pin one approved picture of this outfit for every appearance (None clears)."""
    if look_id is None:
        outfit.fixed_look_id = None
        db.commit()
        return outfit
    look = db.get(HostLook, look_id)
    if look is None or look.host_outfit_id != outfit.id or look.status != "approved":
        raise OutfitError("a fixed picture must be an approved look of this outfit — approve "
                          "or request this look in Host Studio")
    outfit.fixed_look_id = look.id
    db.commit()
    return outfit


def apply_same_outfit(db: Session, case_id: int, outfit_id: int) -> dict:
    """Checkbox A: select the outfit with the same name in every language that has one."""
    source = db.get(HostOutfit, outfit_id)
    if source is None or source.case_id != case_id:
        raise OutfitError("outfit not found")
    applied, skipped = [], []
    for lang in ai_config.channels:
        if lang == source.language:
            continue
        match = _same_named(db, case_id, lang, source.name)
        if match is None:
            skipped.append(lang)
            continue
        select_outfit(db, match)
        applied.append(lang)
    return {"applied": applied, "skipped": skipped,
            "reason": None if applied else "no other language has an outfit with this name"}


def apply_same_picture(db: Session, case_id: int, outfit_id: int, look_id: int) -> dict:
    """Checkbox B: pin the same picture (same pose/camera/framing) in every language whose
    same-named outfit has an approved look like it."""
    source = db.get(HostOutfit, outfit_id)
    look = db.get(HostLook, look_id)
    if source is None or source.case_id != case_id or look is None \
            or look.host_outfit_id != source.id or look.status != "approved":
        raise OutfitError("the picture must be an approved look of the chosen outfit")
    signature = (look.pose, look.camera, look.framing)
    applied, skipped = [], []
    for lang in ai_config.channels:
        if lang == source.language:
            continue
        match = _same_named(db, case_id, lang, source.name)
        twin = match and next((lk for lk in outfit_looks(db, match)
                               if lk.status == "approved"
                               and (lk.pose, lk.camera, lk.framing) == signature), None)
        if match is None or twin is None:
            skipped.append(lang)
            continue
        match.fixed_look_id = twin.id
        applied.append(lang)
    db.commit()
    return {"applied": applied, "skipped": skipped}


def panel(db: Session, case_id: int) -> dict:
    """Everything the Outfits panel shows: per language its outfits with their approved
    looks (as thumbnails), the chosen outfit, the fixed picture, and whether a full film
    of that language was already rendered (a change then needs a new render)."""
    from app.documentary import look_selection as LS

    rows = []
    name_in_languages: dict[str, list[str]] = {}
    for lang in ai_config.channels:
        outfits = _language_outfits(db, case_id, lang)
        for o in outfits:
            name_in_languages.setdefault(o.name.strip().lower(), []).append(lang)
        chosen = selected_outfit(db, case_id, lang)
        rows.append({
            "language": lang,
            "chosen_outfit_id": chosen.id if chosen else None,
            "fixed_look_id": chosen.fixed_look_id if chosen else None,
            "final_rendered": _final_rendered(db, case_id, lang),
            "outfits": [{
                "id": o.id, "name": o.name, "status": o.status,
                "looks": [{"id": lk.id, "stance": lk.stance, "camera": lk.camera,
                           "framing": lk.framing, "pose": lk.pose,
                           "has_image": bool(lk.image_path)}
                          for lk in outfit_looks(db, o) if lk.status == "approved"],
                "to_approve": LS.missing_looks(db, o),
            } for o in outfits],
        })
    return {"case_id": case_id, "languages": rows,
            "name_in_languages": name_in_languages}


def outfit_looks(db: Session, outfit: HostOutfit) -> list[HostLook]:
    return (db.query(HostLook).filter(HostLook.host_outfit_id == outfit.id)
            .order_by(HostLook.id).all())


def studio_supports(language: str, stance: str) -> bool:
    """A seated look needs a studio picture with a seat for the host, a standing look one with
    free floor for him (the safe zones of the language's primary studio picture)."""
    zone = ai_config.host_looks.studio_zone[stance]
    reg = ST.load_registry()
    profile = reg.profile_for(language)
    if profile is None:
        return False
    asset = next((a for a in reg.assets if a.id == profile.primary_background), None)
    return bool(asset and asset.language == language and getattr(asset.safe_zones, zone, None))


def request_look(db: Session, outfit: HostOutfit, camera: str, framing: str, stance: str,
                 pose: str, prompt: str | None = None) -> HostLook:
    """A look of the outfit, in the studio of the outfit's language. Not generated yet."""
    from app.documentary import looks as LK

    if stance not in STANCES:
        raise OutfitError(f"a look sits or stands, not {stance!r}")
    if outfit.status == "retired":
        raise OutfitError("this outfit is retired")
    if not studio_supports(outfit.language, stance):
        raise OutfitError(f"the {outfit.language} studio picture has no "
                          f"{'seat' if stance == 'SEATED' else 'free floor for standing'}")
    return LK.request_outfit_look(db, outfit, camera, framing, stance, pose, prompt)


def request_pose_set(db: Session, outfit: HostOutfit) -> dict:
    """The standard set of looks (host_looks.pose_set) in one click: every entry the outfit does
    not have yet becomes a requested look. Nothing is generated here (one image at a time)."""
    made, kept, skipped = [], [], []
    existing = {(lk.camera, lk.framing, lk.stance): lk for lk in outfit_looks(db, outfit)
                if lk.status != "rejected"}
    for entry in ai_config.host_looks.pose_set:
        key = (entry.camera, entry.framing, entry.stance)
        if key in existing:
            kept.append(existing[key])
            continue
        try:
            made.append(request_look(db, outfit, entry.camera, entry.framing, entry.stance,
                                     entry.pose))
        except OutfitError as e:
            skipped.append({"label": entry.label, "why": str(e)})
    return {"requested": made, "already_there": kept, "skipped": skipped}


def reference_png(outfit: HostOutfit, stance: str) -> bytes:
    """The outfit's picture a look is generated from: the registry picture that fits the stance
    (sitting or standing), else any picture of it, or the outfit's own reference picture."""
    if outfit.outfit_ref:
        return OutfitRegistry().resolve(outfit.outfit_ref).reference_pose(stance).path.read_bytes()
    path = storage.resolve(outfit.reference_image_path)
    if path is None or not path.exists():
        raise OutfitError("the outfit's reference picture is missing")
    return path.read_bytes()
