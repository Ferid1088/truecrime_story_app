"""ThumbnailBrief — everything the composer needs, decided before drawing.

Built only after the episode identity is final: an approved editorial
title and a public status (SOLVED/UNSOLVED). The brief names the host
image of the video's outfit, the real case picture(s), the localized
status badge and the (optional, 0-4 word) text. `validate_brief` is the
last deterministic gate before composition."""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, EpisodeIdentity
from app.identity import titles as T
from app.naming.normalize import norm_title
from app.thumbnails import hosts as H
from app.thumbnails import selection as S


class BriefRefused(ValueError):
    pass


def _seed(case: Case, language: str) -> str:
    return f"{case.case_uid}:{language}"


def default_side(case: Case, language: str) -> str:
    """Host side varies per episode but is stable for it."""
    return "left" if int(hashlib.sha1(_seed(case, language).encode()).hexdigest(), 16) % 2 == 0 \
        else "right"


def build_brief(db: Session, case: Case, language: str, *, side: str | None = None,
                pose: str | None = None, primary_asset_id: int | None = None,
                secondary_asset_id: int | None = None, text: str = "", size: str = "large"
                ) -> dict:
    ident: EpisodeIdentity | None = T.get_identity(db, case.id, language)
    if ident is None or not ident.editorial_title:
        raise BriefRefused("no approved episode title yet — approve a title first")
    label = T.status_label(case.resolution_status if not ident.published else ident.resolution_status,
                           language)
    if label is None:
        raise BriefRefused("the case is neither solved nor unsolved — no status badge possible")
    side = side or default_side(case, language)
    if side not in ("left", "right"):
        raise BriefRefused("side must be left or right")
    outfit = H.outfit_for_episode(db, case.id, language, ident)
    host = H.pick_host(outfit, side, pose=pose, seed=_seed(case, language))

    may_guilt = (case.resolution_status or "") == "SOLVED"
    ranked, rejected = S.rank_assets(db, case, may_state_guilt=may_guilt)
    by_id = {r["id"]: r for r in ranked}

    def chosen(asset_id: int | None, label_: str) -> dict | None:
        if asset_id is None:
            return None
        if asset_id in by_id:
            return by_id[asset_id]
        why = next((r["reason"] for r in rejected if r["id"] == asset_id), "unknown asset")
        raise BriefRefused(f"{label_} picture {asset_id} cannot be used: {why}")

    primary = chosen(primary_asset_id, "primary") or (ranked[0] if ranked else None)
    if primary is None:
        raise BriefRefused("no usable real case picture (verified, rights-cleared, spoiler-free)")
    secondary = chosen(secondary_asset_id, "secondary")
    if secondary is not None:
        if secondary["id"] == primary["id"]:
            raise BriefRefused("secondary picture equals the primary")
        why = S.pair_refusal(primary, secondary, may_guilt)
        if why:
            raise BriefRefused(why)
    visuals = [{"asset_id": primary["id"], "asset_code": primary["code"], "kind": primary["kind"],
                "role": "primary"}]
    if secondary is not None:
        visuals.append({"asset_id": secondary["id"], "asset_code": secondary["code"],
                        "kind": secondary["kind"], "role": "secondary"})
    brief = {
        "channel": T.channel(language)["name"], "channel_id": T.channel(language)["id"],
        "language": language, "case_uid": case.case_uid,
        "episode_title": ident.editorial_title,
        "resolution_status": T.public_status(ident.resolution_status or case.resolution_status),
        "resolution_label": label,
        "host": {"person": "Fereidoun", "side": side, "size": size, "pose": host.pose,
                 "outfit_id": host.outfit_id, "reference_asset": host.id},
        "case_visuals": visuals,
        "thumbnail_text": text.strip(),
        "style": ai_config.thumbnail.style,
        "forbidden": list(ai_config.thumbnail.forbidden),
        "rejected_assets": [{"asset_id": r["id"], "reason": r["reason"]} for r in rejected][:20],
    }
    errors = validate_brief(brief)
    if errors:
        raise BriefRefused("; ".join(errors))
    return brief


def validate_brief(brief: dict) -> list[str]:
    cfg = ai_config.thumbnail
    errs = []
    host = brief.get("host") or {}
    if host.get("person") != "Fereidoun" or not host.get("reference_asset"):
        errs.append("the host (Fereidoun) with an approved reference image is required")
    if not host.get("outfit_id"):
        errs.append("host outfit is missing")
    visuals = brief.get("case_visuals") or []
    if not visuals:
        errs.append("a real case picture is required")
    if len(visuals) > cfg.max_case_images:
        errs.append(f"too many case pictures ({len(visuals)}, max {cfg.max_case_images})")
    if sum(1 for v in visuals if v.get("role") == "primary") != 1:
        errs.append("exactly one primary picture is required")
    label = T.status_label((brief.get("resolution_status") or "").upper(), brief.get("language", "en"))
    if not label or brief.get("resolution_label") != label:
        errs.append("the status badge must carry the localized solved/unsolved label")
    words = len((brief.get("thumbnail_text") or "").split())
    if words > cfg.max_text_words:
        errs.append(f"thumbnail text has {words} words (max {cfg.max_text_words})")
    title_n = norm_title(brief.get("episode_title"))
    if brief.get("thumbnail_text") and norm_title(brief["thumbnail_text"]) == title_n:
        errs.append("thumbnail text repeats the full title")
    return errs
