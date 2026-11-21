"""The three shapes of a Short's cover (reel 9:16, feed 4:5, grid 1:1) inside the cover review: rendered from the cover's title (the planner's or the owner's, never invented here), reviewed and
approved shape by shape, never replacing an approved one by themselves.

A photograph of a real person goes on a cover only when it is authorized: its rights are open (owned, licensed, public domain, Creative Commons) or the owner approved it editorially through
the existing approval. An editorial approval is the owner's judgment that the picture may be used for reporting; it is never taken for permission from the copyright holder, and approving a
cover that carries one needs the owner's explicit acknowledgement. Nothing here makes a paid call, and nothing here sends anything to a platform."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers import proposals as P
from app.covers import service as SV
from app.covers import subject as SU
from app.covers import triptych as T
from app.db.models import (
    Case,
    CoverVariant,
    HostLook,
    HostOutfit,
    PersonPhotoHint,
    ShortClip,
    StoryVersion,
    VisualAsset,
)
from app.documentary import storage
from app.outfits.registry import OutfitRegistry
from app.utils import utc_now

OPEN_RIGHTS = ("owned", "licensed", "public_domain", "creative_commons")
LABELS = {"reel": "Reel 9:16", "feed": "Feed 4:5", "grid": "Profile grid 1:1", "film": "Film 16:9"}


def approved_cover(db: Session, case_id: int, language: str, key: str) -> CoverVariant | None:
    """The approved cover made here for a key (the newest approved version): the 16:9 film cover for "long", the 9:16 reel cover for a Short. None when there is none, its picture is gone or the review
    found something wrong with it. The other shapes are previews and never a platform cover (docs/platform_specs.md)."""
    shape = "film" if key == "long" else "reel"
    row = (db.query(CoverVariant).filter_by(case_id=case_id, language=language, cover_key=key, shape=shape, status="approved").order_by(CoverVariant.version.desc()).first())
    if row is None or json.loads(row.problems_json or "[]") or not storage.resolve(row.file_path).is_file():
        return None
    return row


def file_of_ref(db: Session, ref: str | None) -> Path | None:
    """The picture of a cover reference a publication holds (`film:{id}` or `reel:{id}`, see `approved_cover`), when that cover is approved and its file is there; None for any other reference."""
    shape, _, ident = (ref or "").partition(":")
    if shape not in ("film", "reel") or not ident.isdigit():
        return None
    row = db.get(CoverVariant, int(ident))
    picture = storage.resolve(row.file_path) if row is not None and row.status == "approved" and row.shape == shape else None
    return picture if picture is not None and picture.is_file() else None


def shapes_of(key: str) -> tuple[str, ...]:
    """The shapes a cover key has: the three of a Short, or the one 16:9 shape of the full film (key "long")."""
    return T.FILM_SHAPES if key == "long" else T.SHORT_SHAPES


class VariantError(RuntimeError):
    """`reason` is a short code the interface shows (plate_missing, no_title, unknown_shape, problems, rights_not_acknowledged, ...)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def authorization(asset: VisualAsset) -> tuple[bool, str]:
    """Whether a photograph may be used on a cover, and the plain reason."""
    if asset.rights_status in OPEN_RIGHTS:
        return True, f"rights: {asset.rights_status}" + (f" ({asset.license})" if asset.license else "")
    if asset.rights_status == "editorial_review_required" and asset.editorial_approved_by:
        return True, f"approved editorially by {asset.editorial_approved_by}: this is not copyright permission"
    return False, "not approved yet: " + {"editorial_review_required": "it needs your editorial approval", "permission_required": "its rights holder's permission is required",
                                           "do_not_use": "it is marked do not use"}.get(asset.rights_status, f"its rights are {asset.rights_status}")


def cleared(asset: VisualAsset) -> tuple[bool, str]:
    """Whether the photograph of the person a cover is about may be used: by its rights or editorial approval, else by the owner's standing instruction (config triptych.subject_photo), which
    is an editorial decision and no copyright permission."""
    ok, why = authorization(asset)
    if not ok and asset.cover_photo and asset.rights_status not in ("permission_required", "do_not_use"):
        return True, "chosen by the owner for the covers; rights status '" + str(asset.rights_status) + "': this is not copyright permission"
    standing = T.settings()["subject_photo"]["standing_approval"]
    if ok or not standing["enabled"] or asset.rights_status in ("permission_required", "do_not_use"):
        return ok, why
    return True, f"approved by the {standing['by']}; rights status '{asset.rights_status}': this is not copyright permission"


def subject_candidates(db: Session, case: Case, language: str) -> list[dict]:
    """The people the story shows photographs of, most photographed first (that is how the most important person is guessed, the same in every language: the owner can choose another), each
    with their photographs and whether each may be used. A person is the same person in every language, so the photographs found for the case's films in all languages count together."""
    rows = db.query(PersonPhotoHint).filter_by(case_id=case.id).all()
    people: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: (r.rank, r.id)):
        asset = db.get(VisualAsset, row.visual_asset_id)
        if asset is None or not asset.local_path:
            continue
        person = people.setdefault(row.entity_key, {"entity_key": row.entity_key, "name": row.person_name, "photos": []})
        if all(p["asset_id"] != asset.id for p in person["photos"]):
            ok, why = authorization(asset)
            person["photos"].append({"asset_id": asset.id, "code": asset.asset_code, "authorized": ok, "why": why, "rights_status": asset.rights_status, "license": asset.license, "credit": asset.credit,
                                     "source": asset.source_name or asset.page_url or asset.provider, "width": asset.width, "height": asset.height, "editorial_approved_by": asset.editorial_approved_by,
                                     "image_url": f"/api/visuals/{asset.id}/file"})
    mentions: dict[str, int] = {}
    for row in rows:
        mentions[row.entity_key] = mentions.get(row.entity_key, 0) + 1
    for asset in db.query(VisualAsset).filter(VisualAsset.case_id == case.id, VisualAsset.cover_photo.is_(True), VisualAsset.file_removed_at.is_(None)).order_by(VisualAsset.id):
        key = asset.entity_key or next(iter(json.loads(asset.entities_json or "[]")), None)          # a photo the owner chose for the covers counts for its person even when the inspector never listed it
        if key is None or not asset.local_path:
            continue
        person = people.setdefault(key, {"entity_key": key, "name": key.replace("_", " ").title(), "photos": []})
        if all(p["asset_id"] != asset.id for p in person["photos"]):
            ok, why = authorization(asset)
            person["photos"].append({"asset_id": asset.id, "code": asset.asset_code, "authorized": ok, "why": why, "rights_status": asset.rights_status, "license": asset.license, "credit": asset.credit,
                                     "source": asset.source_name or asset.page_url or asset.provider, "width": asset.width, "height": asset.height, "editorial_approved_by": asset.editorial_approved_by,
                                     "image_url": f"/api/visuals/{asset.id}/file", "cover_photo": True})
        mentions[key] = mentions.get(key, 0) + 100                # the owner's choice makes this person the one the covers show
    return sorted(people.values(), key=lambda p: (-mentions[p["entity_key"]], p["name"]))


def _film_outfit(db: Session, case_id: int, language: str) -> tuple[str | None, dict, str | None]:
    """(outfit id, what it came from, why none) for the full film: the outfit of the film's first host scene that has a video (as the film's own look is chosen), else the outfit selected for the language."""
    from app.db.models import HostScene
    from app.documentary import outfits as OF

    scene = (db.query(HostScene).filter(HostScene.case_id == case_id, HostScene.language == language, HostScene.avatar_video_path.isnot(None)).order_by(HostScene.id).first())
    outfit = OF.scene_outfit(db, scene) if scene is not None else db.query(HostOutfit).filter_by(case_id=case_id, language=language, status="selected").order_by(HostOutfit.id.desc()).first()
    if outfit is None or not outfit.outfit_ref:
        return None, {}, f"The {language} film has no host outfit yet: the host's picture cannot be chosen."
    return outfit.outfit_ref, {"from": "the outfit of the film's first host scene" if scene is not None else "the outfit selected for the language", "host_outfit_id": outfit.id}, None


def _clip_outfit(db: Session, case_id: int, language: str, key: str) -> tuple[str | None, dict, str | None]:
    """(outfit id, what it came from, why none): the outfit of the clip's avatar video, read from the provider look the clip recorded; a language whose clip is not made yet uses the
    outfit the case has selected for that language."""
    rows = db.query(ShortClip).filter_by(case_id=case_id, language=language, clip_key=key).order_by(ShortClip.version.desc(), ShortClip.id.desc()).all()
    hooks = [(row, ((json.loads(row.media_json or "{}").get("avatars") or {}).get("hook") or {}).get("look_id")) for row in rows]
    row, look_id = next(((r, i) for r, i in hooks if i), (None, None))
    if look_id is not None:
        look = db.query(HostLook).filter_by(heygen_look_id=look_id).first()
        if look is None:
            return None, {}, f"The look {look_id} of the {language} {key} video is not in the library: the host's outfit is unknown."
        return look.outfit_id, {"from": "the clip's avatar video", "look_id": look_id, "clip_id": row.id}, None
    chosen = db.query(HostOutfit).filter_by(case_id=case_id, language=language, status="selected").order_by(HostOutfit.id.desc()).first()
    if chosen is None or not chosen.outfit_ref:
        return None, {}, f"There is no {language} avatar video for {key} and no selected {language} outfit: the host's picture cannot be chosen."
    return chosen.outfit_ref, {"from": "the outfit selected for the language (the clip is not made yet)", "host_outfit_id": chosen.id}, None


def plate_for(db: Session, case_id: int, language: str, key: str) -> tuple[Path | None, dict | None, str | None]:
    """The host's still for the cover: the posed, front-facing picture of the outfit the clip's avatar video was made in (calm and sharp, no half-spoken word as in a video frame), as
    (picture, what it came from, and why there is none)."""
    outfit_id, source, why = _film_outfit(db, case_id, language) if key == "long" else _clip_outfit(db, case_id, language, key)
    if outfit_id is None:
        return None, None, why
    try:
        outfit = OutfitRegistry().resolve(outfit_id)
    except KeyError:
        return None, None, f"The outfit {outfit_id} has no pictures in the outfit library."
    roles = T.settings()["plate"]["pose_roles"]          # the first the outfit has: a calm, front-facing standing picture (some outfits have no "front" one, only "stand")
    role = next((r for r in roles if r in outfit.poses), None)
    if role is None:
        return None, None, f"The outfit {outfit_id} has none of the pictures {', '.join(roles)}."
    return outfit.poses[role].path, {**source, "outfit": outfit_id, "pose": role}, None


def banner_name(db: Session, case_id: int, language: str, latin: str) -> tuple[str, dict]:
    """How the banner writes a person's name in `language`: the spelling the owner confirmed for that language (config `triptych.native_names`), used only when the language's own story
    text contains it verbatim; otherwise the name as it is. The check is returned as evidence."""
    spelling = T.settings()["native_names"].get(latin, {}).get(language)
    if not spelling:
        return latin, {"source": "name as stored", "checked_in_story": None}
    texts = [row.story_text for row in db.query(StoryVersion).filter_by(case_id=case_id, language=language).all()]
    found = sum(text.count(spelling) for text in texts)
    return (spelling, {"source": "confirmed spelling", "checked_in_story": found, "story_versions": len(texts)}) if found else (latin, {"source": "name as stored", "checked_in_story": 0, "note": f"{spelling} is not in the {language} story"})


def _evidence(proposal, plate: Path, source: dict, photo: VisualAsset | None, subject: str, metrics: dict, name_check: dict, choice: dict | None = None) -> dict:
    evidence: dict = {"title_status": proposal.status, "title_source": proposal.source, "title_confidence": proposal.confidence, "plate": plate.name, "host_picture": source,
                      "subject": subject or None, "subject_choice": choice, "name_check": name_check, "measured": metrics, "paid_calls": 0}
    if photo is not None:
        ok, why = cleared(photo)
        evidence["photo"] = {"asset_id": photo.id, "code": photo.asset_code, "authorized": ok, "why": why, "rights_status": photo.rights_status, "license": photo.license, "credit": photo.credit,
                             "source": photo.source_name or photo.page_url or photo.provider, "sha256": photo.sha256, "copyright_note": T.settings()["rights_note"]}
    return evidence


def _next_version(db: Session, case_id: int, language: str, key: str, shape: str) -> int:
    newest = (db.query(CoverVariant).filter_by(case_id=case_id, language=language, cover_key=key, shape=shape).order_by(CoverVariant.version.desc()).first())
    return (newest.version if newest else 0) + 1


def render(db: Session, case: Case, language: str, key: str, *, shapes: list[str] | None = None, highlight: str | None = None, subject_entity: str | None = None,
           use_photo: bool = True, photo_asset_id: int | None = None, subject_name: str | None = None) -> list[CoverVariant]:
    """New versions (status proposed) of the chosen shapes (all three by default). The title is the cover's current one; the person is `subject_entity` (the most photographed when not given,
    "none" for no person; `subject_name` is how the banner writes the name, the person's name as the story holds it when not given); the photograph is `photo_asset_id`, else the first authorized one of that person, and none when nothing is authorized or `use_photo` is false. Approved versions are
    never touched."""
    from app.covers import text_gate as TG

    try:
        TG.guard(db, case, language)                                    # a language whose texts the owner reviews gets no cover before he has released them
    except TG.TextsNotReleased as exc:
        raise VariantError("texts_not_released", str(exc)) from exc
    shapes = shapes or list(shapes_of(key))
    if bad := [s for s in shapes if s not in shapes_of(key)]:
        raise VariantError("unknown_shape", f"Unknown shape {bad[0]} for {key}: {', '.join(shapes_of(key))}.")
    proposal = P.current(db, case.id, language).get(key)
    if proposal is None or not proposal.title.strip() or proposal.status == "skipped":
        raise VariantError("no_title", f"There is no {language} title for {key}: the cover planner has to propose one first (no text is invented here).")
    plate, source, why = plate_for(db, case.id, language, key)
    if plate is None:
        raise VariantError("plate_missing", why or "no host picture")
    out_dir = SV.covers_dir(case.id, language)
    people = subject_candidates(db, case, language)
    chosen_by, roles = {"by": "owner"}, []
    if subject_entity is None and people:
        picked, why, roles = asyncio.run(SU.choose(db, case, people))
        subject_entity, chosen_by = (picked, {"by": "rule", "why": why}) if picked else (people[0]["entity_key"], {"by": "most photographed", "why": why})
    person = None if subject_entity == "none" else next((p for p in people if p["entity_key"] == subject_entity), None)
    photo = None
    if person is not None and use_photo:
        found = [db.get(VisualAsset, p["asset_id"]) for p in person["photos"] if photo_asset_id is None or p["asset_id"] == photo_asset_id]
        usable = [a for a in found if a is not None and cleared(a)[0]]
        solo = [a for a in usable if json.loads(a.entities_json or "[]") == [person["entity_key"]] and a.subject_type in ("person", "portrait_person")]        # the person alone, not in a group
        chosen_by_owner = [a for a in usable if a.cover_photo]                                                  # what the owner chose in the picture review is used first
        photo = max(chosen_by_owner or solo or usable, key=lambda a: (a.width or 0) * (a.height or 0)) if usable else None          # the sharpest, largest picture of the person
    named, name_check = ((subject_name or "").strip(), {"source": "typed by the owner"}) if (subject_name or "").strip() else banner_name(db, case.id, language, person["name"]) if person else ("", {})
    chosen = (highlight or T.key_phrase(proposal.title, language)).strip()
    made = T.compose_set(language, proposal.title, plate, storage.ROOT / ai_config.channels[language].logo, highlight=chosen, subject=named if person else None, shapes=tuple(shapes),
                         photo=storage.resolve(photo.local_path) if photo else None, seed=f"{case.id}-{language}-{key}")
    earlier = db.query(CoverVariant).filter(CoverVariant.case_id == case.id, CoverVariant.language == language, CoverVariant.cover_key == key, CoverVariant.shape.in_(shapes),
                                            CoverVariant.status == "proposed").all()
    rows = []
    for shape in shapes:
        version = _next_version(db, case.id, language, key, shape)
        path = out_dir / f"tri_{key}_{shape}_v{version}.jpg"
        made[shape]["image"].save(path, "JPEG", quality=92, optimize=True)
        image = made[shape]["image"]
        row = CoverVariant(case_id=case.id, language=language, cover_key=key, shape=shape, version=version, status="proposed", proposal_id=proposal.id, title=proposal.title, highlight=chosen,
                           subject_name=named if person else "", photo_asset_id=photo.id if photo else None, file_path=storage.rel(path) or "",
                           file_hash=hashlib.sha256(path.read_bytes()).hexdigest(), width=image.width, height=image.height, elements_json=json.dumps(made[shape]["elements"]),
                           problems_json=json.dumps(made[shape]["problems"], ensure_ascii=False),
                           evidence_json=json.dumps(_evidence(proposal, plate, source, photo, named if person else "", made[shape]["metrics"], name_check, {**chosen_by, "roles": roles}), ensure_ascii=False))
        db.add(row)
        rows.append(row)
    for old in earlier:
        old.status = "superseded"                                          # an older proposal the new render replaces; an approved version is never touched
    db.commit()
    rule = T.settings()["approval"]
    if rule["auto_approve_upload_shapes"]:
        for row in rows:
            if row.shape in rule["upload_shapes"] and not json.loads(row.problems_json):
                approve(db, row.id, actor="automatic")                     # the shapes that get uploaded are approved by the app itself when they have no problem
    return rows


def _row(db: Session, variant_id: int) -> CoverVariant:
    row = db.get(CoverVariant, variant_id)
    if row is None:
        raise VariantError("not_found", "Cover version not found.")
    return row


def approve(db: Session, variant_id: int, *, actor: str = "owner", acknowledge_rights: bool = False) -> CoverVariant:
    """The owner's approval of one shape's version. A version with problems is refused; one that carries a photograph needs the acknowledgement that an editorial approval is not copyright
    permission, and the photograph must still be authorized. The approved version before it (if any) becomes `superseded` and keeps its file."""
    row = _row(db, variant_id)
    if row.status != "proposed":
        raise VariantError("not_proposed", f"A version that is {row.status} cannot be approved.")
    if problems := json.loads(row.problems_json):
        raise VariantError("problems", "This version has problems: " + "; ".join(problems))
    if row.photo_asset_id is not None:
        asset = db.get(VisualAsset, row.photo_asset_id)
        ok, why = authorization(asset) if asset else (False, "the photograph is gone")
        if not ok:
            raise VariantError("photo_not_authorized", f"The photograph on this cover is not authorized any more: {why}.")
        if T.settings()["approval"]["rights_acknowledgement_required"] and not acknowledge_rights:
            raise VariantError("rights_not_acknowledged", "This cover carries a photograph of a real person. " + T.settings()["rights_note"] + " Confirm that you know this to approve it.")
    for older in db.query(CoverVariant).filter_by(case_id=row.case_id, language=row.language, cover_key=row.cover_key, shape=row.shape, status="approved").all():
        older.status = "superseded"
    row.status, row.approved_by, row.approved_at = "approved", actor, utc_now()
    db.commit()
    return row


def reject(db: Session, variant_id: int) -> CoverVariant:
    row = _row(db, variant_id)
    if row.status != "proposed":
        raise VariantError("not_proposed", f"A version that is {row.status} cannot be rejected.")
    row.status = "rejected"
    db.commit()
    return row


def view(row: CoverVariant) -> dict:
    return {"id": row.id, "shape": row.shape, "label": LABELS[row.shape], "version": row.version, "status": row.status, "title": row.title, "highlight": row.highlight, "subject": row.subject_name or None,
            "photo_asset_id": row.photo_asset_id, "width": row.width, "height": row.height, "problems": json.loads(row.problems_json), "evidence": json.loads(row.evidence_json),
            "image_url": f"/api/cover-variants/{row.id}/image", "approved_by": row.approved_by, "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None}


def platforms(key: str) -> list[dict]:
    """Which cover each platform really receives and which shapes are only previews: the platforms take one cover per video, so the other shapes are never uploaded."""
    kind = "film" if key == "long" else "short"
    return [{"destination": dest, "takes": use["takes"], "candidate_shape": use["candidate"], "uploaded_now": use["now"].replace("<key>", key), "preview_only": [s for s in shapes_of(key) if s != use["candidate"]]}
            for dest, use in T.settings()["platform_use"].items() if dest.endswith(f"/{kind}")]


def overview(db: Session, case: Case, language: str, key: str) -> dict:
    """Everything the review shows for one Short: the newest version of each shape (and the approved one when it is an older version), the history, the hook, the person and photographs
    with their authorization, the studio frame, and the platforms."""
    proposal = P.current(db, case.id, language).get(key)
    plate, source, why = plate_for(db, case.id, language, key)
    shapes: dict[str, dict] = {}
    for shape in shapes_of(key):
        rows = db.query(CoverVariant).filter_by(case_id=case.id, language=language, cover_key=key, shape=shape).order_by(CoverVariant.version.desc()).all()
        approved = next((r for r in rows if r.status == "approved"), None)
        newest = next((r for r in rows if r.status in ("proposed", "approved")), None)
        shapes[shape] = {"label": LABELS[shape], "newest": view(newest) if newest else None, "approved": view(approved) if approved else None, "history": [view(r) for r in rows]}
    return {"case_id": case.id, "language": language, "key": key, "shapes": shapes, "platforms": platforms(key), "rights_note": T.settings()["rights_note"],
            "hook": {"title": proposal.title if proposal else None, "status": proposal.status if proposal else None, "confidence": proposal.confidence if proposal else None, "reason": proposal.reason if proposal else None},
            "plate": {"available": plate is not None, "outfit": source["outfit"] if source else None, "problem": why},
            "people": subject_candidates(db, case, language), "can_render": plate is not None and proposal is not None and bool(proposal.title.strip()) and proposal.status != "skipped"}
