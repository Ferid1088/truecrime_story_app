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
from app.db.models import HostLook, HostOutfit, OutfitItem
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


def selected_library_item_ids(db: Session, outfit: HostOutfit) -> list[int]:
    """Return library photos currently used by this video's outfit.

    The source is intentionally derived from the look records, so this works with
    existing databases without adding a schema migration. New attachments store the
    stable OutfitItem id; older attachments are matched by their stored path.
    """
    if not outfit.outfit_ref or "/" not in outfit.outfit_ref:
        return []
    studio, outfit_slug = outfit.outfit_ref.split("/", 1)
    rows = (db.query(OutfitItem)
            .filter(OutfitItem.studio == studio, OutfitItem.outfit_slug == outfit_slug)
            .all())
    by_path = {row.file_path: row.id for row in rows}
    selected: set[int] = set()
    for look in outfit_looks(db, outfit):
        source = look.image_model or ""
        if source.startswith("owner_upload:library:item_id:"):
            try:
                selected.add(int(source.split(":", 3)[3]))
            except ValueError:
                pass
        elif source.startswith("owner_upload:library:"):
            path = source.removeprefix("owner_upload:library:")
            if path in by_path:
                selected.add(by_path[path])
    return sorted(selected)


def reuse_library_photos(db: Session, outfit: HostOutfit,
                         selected_item_ids: list[int] | None = None) -> dict:
    """Attach existing outfit-library photos to requested looks without image generation.

    The library stores real, already-created outfit pictures, while ``HostLook`` stores the
    camera/framing/stance slot the stage director uses. Reusing a library picture is therefore
    an explicit import into the look record. The Autopilot start choice approves the imported
    look; the Autopilot avatar step uploads it to HeyGen when the film needs it.
    """
    from app.documentary import looks as LK
    from app.outfits import library as outfit_library

    if not outfit.outfit_ref or "/" not in outfit.outfit_ref:
        raise OutfitError("this outfit has no library reference")
    studio, outfit_slug = outfit.outfit_ref.split("/", 1)
    if studio != outfit.language:
        raise OutfitError("the outfit library studio does not match this language")

    # Make the new button a complete no-generation path: if the user has not requested
    # the standard slots yet, create the empty look records first, then attach the files.
    if not any(look.status == "requested" for look in outfit_looks(db, outfit)):
        request_pose_set(db, outfit)

    query = (db.query(OutfitItem)
            .filter(OutfitItem.studio == studio, OutfitItem.outfit_slug == outfit_slug,
                    OutfitItem.status == "available")
            .order_by(OutfitItem.id).all())
    available = query
    if selected_item_ids is not None:
        wanted = set(selected_item_ids)
        if not wanted:
            raise OutfitError("select at least one outfit photo for this film")
        rows = [row for row in available if row.id in wanted]
        foreign = wanted - {row.id for row in rows}
        if foreign:
            raise OutfitError("one or more selected photos do not belong to this outfit")
    else:
        rows = available
    if not rows:
        raise OutfitError("no available library photos found for this outfit")

    by_stance = {
        "SEATED": [row for row in rows if row.stance == "sitting"],
        "STANDING": [row for row in rows if row.stance == "standing"],
    }
    attached, skipped = [], []
    offsets = {stance: 0 for stance in by_stance}
    for look in outfit_looks(db, outfit):
        if look.status == "approved":
            # Changing the source photo invalidates the previous owner approval, but
            # never silently replaces a look that has already been uploaded to HeyGen.
            if look.heygen_look_id:
                skipped.append({"label": f"look #{look.id}",
                                "why": "already uploaded to HeyGen — cannot replace it"})
                continue
            look.status = "generated"
        if look.status not in ("requested", "generated", "rejected"):
            continue
        candidates = by_stance.get(look.stance, [])
        if not candidates:
            skipped.append({"label": f"look #{look.id}",
                            "why": f"no {look.stance.lower()} library photo is available"})
            continue
        row = candidates[offsets[look.stance] % len(candidates)]
        offsets[look.stance] += 1
        path = Path(row.file_path)
        if not path.is_absolute():
            path = outfit_library.ROOT / path
        if not path.is_file():
            skipped.append({"label": f"look #{look.id}",
                            "why": f"library photo is missing on disk: {row.file_path}"})
            continue
        LK.attach_picture(db, look, path.read_bytes(), f"library:item_id:{row.id}")
        attached.append({"look_id": look.id, "photo": row.file_path,
                         "item_id": row.id,
                         "stance": look.stance, "camera": look.camera,
                         "framing": look.framing})
    return {"attached": attached, "skipped": skipped,
            "selected_item_ids": selected_library_item_ids(db, outfit)}


# ---------------------------------------------------------------------------
# the choice of an outfit and its photos for a video (the Autopilot start and the host window)
# ---------------------------------------------------------------------------


def library_photos(db: Session, outfit_ref: str | None, selected_ids: list[int] | None = None) -> list[dict]:
    """The photos of a library outfit (`<studio>/<outfit>`), with those already used marked."""
    if not outfit_ref or "/" not in outfit_ref:
        return []
    studio, slug = outfit_ref.split("/", 1)
    selected = set(selected_ids or [])
    return [{"id": item.id, "situation": item.situation, "stance": item.stance,
             "status": item.status, "selected": item.id in selected}
            for item in (db.query(OutfitItem)
                         .filter(OutfitItem.studio == studio, OutfitItem.outfit_slug == slug)
                         .order_by(OutfitItem.id).all())]


def choice_options(db: Session, case_id: int | None, language: str) -> dict:
    """The outfits that can be chosen for a language: those the case already has, then the library's others, each with
    its photos. `selected` is the key of the outfit the video uses now."""
    options = []
    existing = [] if case_id is None else _language_outfits(db, case_id, language)
    newest: dict[str, HostOutfit] = {}
    for outfit in existing:          # one entry per library outfit: the selected one, else the newest
        slot = outfit.outfit_ref or f"own:{outfit.id}"
        kept = newest.get(slot)
        if kept is None or outfit.status == "selected" or (kept.status != "selected" and outfit.id > kept.id):
            newest[slot] = outfit
    for outfit in sorted(newest.values(), key=lambda o: o.id):
        options.append({"key": f"existing:{outfit.id}", "name": outfit.name, "ref": outfit.outfit_ref,
                        "source": "case", "status": outfit.status, "selected": outfit.status == "selected",
                        "photos": library_photos(db, outfit.outfit_ref, selected_library_item_ids(db, outfit))})
    taken = {o.outfit_ref for o in existing if o.outfit_ref}
    for registered in OutfitRegistry().all(language):
        if registered.outfit_id not in taken:
            options.append({"key": f"registry:{registered.outfit_id}", "name": registered.label,
                            "ref": registered.outfit_id, "source": "library", "status": "available",
                            "selected": False, "photos": library_photos(db, registered.outfit_id)})
    return {"language": language, "selected": next((o["key"] for o in options if o["selected"]), None),
            "options": options}


def resolve_choice(db: Session, case_id: int, language: str, key: str) -> tuple[HostOutfit | None, str | None]:
    """(the case's outfit, None) for `existing:<id>`; (the case's outfit of that library outfit or None, its ref) for
    `registry:<ref>`."""
    if key.startswith("existing:"):
        try:
            outfit_id = int(key.removeprefix("existing:"))
        except ValueError as exc:
            raise OutfitError(f"Invalid outfit choice for {language}.") from exc
        outfit = db.get(HostOutfit, outfit_id)
        if outfit is None or outfit.case_id != case_id or outfit.language != language or outfit.status == "retired":
            raise OutfitError(f"That outfit choice is not available for {language}.")
        return outfit, None
    if not key.startswith("registry:"):
        raise OutfitError(f"Invalid outfit choice for {language}.")
    ref = key.removeprefix("registry:")
    try:
        registered = OutfitRegistry().resolve(ref)
    except KeyError as exc:
        raise OutfitError(f"That outfit is no longer available for {language}.") from exc
    if registered.language != language:
        raise OutfitError(f"That outfit belongs to another language, not {language}.")
    outfit = (db.query(HostOutfit)
              .filter(HostOutfit.case_id == case_id, HostOutfit.language == language,
                      HostOutfit.outfit_ref == ref, HostOutfit.status != "retired")
              .order_by(HostOutfit.id).first())
    return outfit, ref


def apply_choice(db: Session, case_id: int, language: str, key: str, item_ids: list[int],
                 approved_by: str) -> dict:
    """Use this outfit and these library photos for the video: the outfit is made when the case has none yet and
    selected, the photos become its looks, and the owner's choice approves them (no image model is called)."""
    outfit, ref = resolve_choice(db, case_id, language, key)
    library_ref = outfit.outfit_ref if outfit is not None else ref
    if library_ref and not item_ids:
        raise OutfitError(f"Choose at least one existing outfit photo for: {language}")
    if library_ref:                   # nothing is saved for a photo that is not this outfit's
        photos = library_photos(db, library_ref)
        available = {p["id"] for p in photos if p["status"] == "available"}
        if set(item_ids) - available:
            raise OutfitError("one or more selected photos do not belong to this outfit")
        usable = {p["id"] for p in photos if p["stance"] in ("sitting", "standing")}
        if set(item_ids) - usable:
            raise OutfitError("one or more selected photos are not a sitting or standing pose: choose another")
    if outfit is None:
        outfit = create_outfit(db, case_id, language, OutfitRegistry().resolve(ref).label, outfit_ref=ref)
    select_outfit(db, outfit)
    done = {"attached": [], "skipped": []}
    if outfit.outfit_ref:
        done = reuse_library_photos(db, outfit, selected_item_ids=item_ids)
        for attached in done["attached"]:
            look = db.get(HostLook, attached["look_id"])
            if look is not None:
                look.status, look.approved_by, look.approved_at = "approved", approved_by, utc_now()
        db.commit()
    return {"outfit": outfit, "attached": done["attached"], "skipped": done["skipped"]}


def reference_png(outfit: HostOutfit, stance: str) -> bytes:
    """The outfit's picture a look is generated from: the registry picture that fits the stance
    (sitting or standing), else any picture of it, or the outfit's own reference picture."""
    if outfit.outfit_ref:
        return OutfitRegistry().resolve(outfit.outfit_ref).reference_pose(stance).path.read_bytes()
    path = storage.resolve(outfit.reference_image_path)
    if path is None or not path.exists():
        raise OutfitError("the outfit's reference picture is missing")
    return path.read_bytes()
