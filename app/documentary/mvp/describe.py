"""Just before the film is planned: what each approved picture or clip shows, and which of the case's people it shows. Only approved
items are looked at (a rejected picture costs nothing); the card is kept on the asset (`spec.picture_card`, tied to the file's
sha256) and asked for once. The director reads it, so a person the words name is matched to the pictures that show them."""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.concurrency import limit_size
from app.db.models import Case, VisualAsset, VisualPlan
from app.documentary.mvp.media import image_for_check
from app.documentary.visuals import images as IM
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)


def card_of(asset: VisualAsset) -> dict | None:
    """The picture's card, if it was made for this very file."""
    card = json.loads(asset.spec_json or "{}").get("picture_card")
    return card if isinstance(card, dict) and card.get("sha") == asset.sha256 else None


def people_of(asset: VisualAsset) -> list[str]:
    """The entity keys of the case's people the card says the picture shows."""
    card = card_of(asset)
    return [p["entity"] for p in (card or {}).get("people") or [] if isinstance(p, dict) and p.get("entity")]


def depicts(asset: VisualAsset) -> str:
    """What the director reads as the picture's content: the card, else the verifier's sentence, else the title."""
    card = card_of(asset)
    if card and card.get("shows"):
        return f"{card['shows']} ({card.get('setting') or card.get('kind') or ''})".replace(" ()", "")[:260]
    return (asset.description or asset.title or "")[:260]


def valid(data, entity_keys: set[str]) -> dict | None:
    """The card in the answer, with only the people that exist in the case; None when the answer is not a card."""
    if not isinstance(data, dict) or not str(data.get("shows") or "").strip():
        return None
    people = [{"entity": p["entity"], "sure": "yes" if p.get("sure") == "yes" else "likely"} for p in data.get("people") or []
              if isinstance(p, dict) and p.get("entity") in entity_keys]
    return {"shows": str(data["shows"]).strip()[:300], "people": people, "setting": str(data.get("setting") or "")[:120],
            "kind": str(data.get("kind") or "other")[:20]}


async def describe_asset(db: Session, case: Case, asset: VisualAsset, entities: list[dict]) -> bool:
    payload = {"case": case.canonical_title,
               "media": "video: start, middle and end frames, left to right" if asset.asset_type == "video" else "image",
               "title": asset.title, "caption": asset.caption, "source": asset.source_name, "owner_note": asset.owner_note,
               "entity_list": [{k: e.get(k) for k in ("key", "name", "type", "description", "not_this") if e.get(k)}
                               for e in entities]}
    try:
        image = IM.data_url(await asyncio.to_thread(image_for_check, asset))
        with track_run(db, case.id, "Main Visual Planner: describe", input_summary=asset.asset_code) as run:
            data, res = await asyncio.wait_for(
                run_agent("mvp.describe", get_generation_provider(), json.dumps(payload, ensure_ascii=False), images=[image]),
                ai_config.mvp().call_timeout_s)
            stamp_run(run, res, "mvp_picture_describer")
    except (GenerationError, OSError, IM.ImageError, TimeoutError) as e:
        log.warning("describe %s failed: %s", asset.asset_code, type(e).__name__)
        return False
    card = valid(data, {e["key"] for e in entities if e.get("key")})
    if card is None:
        return False
    spec = json.loads(asset.spec_json or "{}")
    spec["picture_card"] = {**card, "sha": asset.sha256}
    asset.spec_json = json.dumps(spec, ensure_ascii=False)
    return True


async def describe_approved(db: Session, case: Case, assets: list[VisualAsset], entities: list[dict]) -> dict:
    """A card for every approved picture or clip that has none for its file, stopping cleanly when the case's cap is reached."""
    todo = [a for a in assets if card_of(a) is None]
    stats = {"approved": len(assets), "described": 0, "failed": 0, "budget_stopped": False}
    money = BudgetService(db)
    gate = asyncio.Semaphore(limit_size("vision"))

    async def one(asset: VisualAsset) -> None:
        async with gate:
            if stats["budget_stopped"]:
                return
            snap = money.snapshot(case.id)
            if snap.spent_eur >= snap.cap_eur - 1e-9:
                stats["budget_stopped"] = True
                return
            stats["described" if await describe_asset(db, case, asset, entities) else "failed"] += 1

    await asyncio.gather(*(one(a) for a in todo))
    db.commit()
    return stats


def approved_assets(db: Session, plan: VisualPlan) -> list[VisualAsset]:
    """The approved pictures and clips of the film: this round's and the earlier rounds' (see `direct.approved_items`)."""
    from app.documentary.mvp import direct as DR

    seen: dict[int, VisualAsset] = {}
    for group in DR.approved_items(db, plan).values():
        for item in group:
            seen.setdefault(item.asset.id, item.asset)
    return list(seen.values())


def portrait_assets(db: Session, case_id: int, plan: VisualPlan) -> list[VisualAsset]:
    """The photos the owner chose for the pencil drawing that are not among the approved ones already (their card says who they show)."""
    have = {a.id for a in approved_assets(db, plan)}
    return [a for a in db.query(VisualAsset).filter(VisualAsset.case_id == case_id, VisualAsset.draw_priority.isnot(None)).all()
            if a.id not in have]
