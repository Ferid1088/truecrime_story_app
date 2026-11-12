"""The pictures of a clip's middle: real pictures the language's film shows, the ones whose words say most about what the clip tells.

Only pictures the film may use (verified for the case, rights allowed and, for a news photo, approved by a person), no spoiler, nothing graphic or from custody, and no
face of a victim, a suspect or any person (config short_clips.video). The choice is by rule: how many words of the clip's context and detail the picture's description
shares, then the order the film shows them. The owner checks pictures himself."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, ProductionScript, VisualAsset
from app.documentary import storage
from app.documentary.visuals import rights as R
from app.documentary.visuals.spoilers import revealed_by, shows_custody
from app.thumbnails import selection as S

_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)


class NoPictures(RuntimeError):
    pass


def shown_codes(db: Session, case_id: int, language: str) -> list[str]:
    """The asset codes of the pictures the language's newest production script shows, in the order they first appear."""
    row = (db.query(ProductionScript).filter(ProductionScript.case_id == case_id, ProductionScript.language == language).order_by(ProductionScript.id.desc()).first())
    seen: list[str] = []
    for shot in json.loads(row.script_json or "{}").get("shots", []) if row else []:
        code = shot.get("asset_id")
        if shot.get("kind") == "image" and code and code not in seen:
            seen.append(code)
    return seen


def reject_reason(asset: VisualAsset, kind: str, late: set[str]) -> str | None:
    """Why a picture the film shows is no picture of a clip (None when it can be one)."""
    cfg = ai_config.short_clips_settings().video
    if asset.verification_status != "verified":
        return "not verified for this case"
    if asset.asset_type not in S.IMAGE_TYPES or asset.provider == "generated" or asset.found_during == "generated":
        return "not a real still picture"
    if not R.usable(asset):
        return f"rights: {asset.rights_status} (not cleared for the film)"
    if kind in cfg.picture_forbidden_kinds:
        return f"a {kind} is not shown in a clip"
    if (asset.width or 0) < cfg.picture_min_px[0] or (asset.height or 0) < cfg.picture_min_px[1]:
        return f"resolution too low ({asset.width}x{asset.height})"
    if revealed_by(asset) & late:
        return "shows something the story reveals later (spoiler)"
    if shows_custody(asset):
        return "shows custody or court (spoils the outcome)"
    verification = json.loads(asset.verification_json or "{}")
    seen = " ".join(str(x or "") for x in (asset.description, asset.caption, asset.subject_type, verification.get("depicts"), verification.get("subject_type"))).lower() + " "
    terms = ai_config.covers_settings().scene["forbidden_terms"] + ai_config.thumbnail.graphic_terms
    if asset.entity_type == "person" or any(f" {t.strip()} " in f" {seen} " for t in terms):
        return "shows a person, a weapon or something graphic"
    return None


def words_of(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")}


def ranked(found: list[dict], used: dict[str, int]) -> list[dict]:
    """The pictures other clips show least first, then the ones whose words say most about this clip, then the film's order."""
    return sorted(found, key=lambda f: (used.get(f["code"], 0), -f["shared"], f["order"]))


def choose(db: Session, case: Case, language: str, clip: dict, count: int, used: dict[str, int] | None = None) -> list[dict]:
    """`count` pictures for a clip: [{code, path, what, shared}]. The pictures other clips of the language show least come first (none at all before any that another
    clip shows), then those whose words say most about this clip. NoPictures when the film shows none that can be used."""
    shown = shown_codes(db, case.id, language)
    late = S.late_reveal_ids(db, case.id)
    wanted = words_of(f"{clip.get('context', '')} {clip.get('detail', '')}")
    found = []
    for asset in db.query(VisualAsset).filter(VisualAsset.case_id == case.id, VisualAsset.asset_code.in_(shown or [""])).all():
        kind = S.asset_kind(asset)
        if reject_reason(asset, kind, late) is not None or not asset.local_path or not storage.resolve(asset.local_path).exists():
            continue
        what = " ".join(str(x) for x in (asset.description, asset.caption) if x)
        found.append({"code": asset.asset_code, "path": storage.resolve(asset.local_path), "what": what[:160], "shared": len(wanted & words_of(what)), "order": shown.index(asset.asset_code)})
    if not found:
        raise NoPictures(f"the {language} film shows no picture that a clip may use")
    return [{k: v for k, v in f.items() if k != "order"} for f in ranked(found, used or {})[:count]]
