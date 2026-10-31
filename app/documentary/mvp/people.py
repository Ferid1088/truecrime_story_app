"""The person-photo inspector: before the planner plans, it finds the persons the text names for the first time, and for each the approved
photos that give nothing away at that point of the text. Its answer is a table (`person_photo_hints`: where in the text, which photo, in
which order) that the main planner reads; the planner uses the photos where its own rules allow. Only approved photos are looked at."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, PersonPhotoHint, VisualAsset, VisualPlan
from app.documentary.mvp import describe as DS
from app.documentary.mvp.direct import Item, readable, reveal_blocked
from app.documentary.production.picture_rules import named_by
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)
MAX_PHOTOS_ASKED = 8          # the best candidates of a person the agent is asked about
MAX_HINTS = 5                 # the photos kept for each person
CALLS_AT_ONCE = 4
TOLD_SO_FAR_CHARS = 3000


@dataclass
class FirstMention:
    key: str
    name: str
    start: float
    end: float
    sentence: str
    beat_id: str | None


def first_mentions(sentences: list[dict], persons: list[dict], beat_at) -> list[FirstMention]:
    """For each person of the case the first sentence that names them (full name or first name), in the order of the film."""
    out = []
    for person in persons:
        hit = next((s for s in sorted(sentences, key=lambda s: s["start"])
                    if named_by(s.get("display") or s.get("speech") or "", person["key"])), None)
        if hit is not None:
            out.append(FirstMention(person["key"], person.get("name") or readable(person["key"]) or person["key"], hit["start"], hit["end"],
                                    (hit.get("display") or hit.get("speech") or "")[:300], beat_at(hit["start"])))
    return sorted(out, key=lambda m: m.start)


def candidates(items: dict[str, list[Item]], key: str) -> list[Item]:
    """The approved photos of this person (this round's and the earlier rounds'): a photo the card says shows only them first."""
    seen: dict[str, Item] = {}
    for group in items.values():
        for item in group:
            if item.asset.asset_type == "photo" and key in item.people:
                seen.setdefault(item.code, item)
    return sorted(seen.values(), key=lambda i: (len(i.people), i.rank))[:MAX_PHOTOS_ASKED]


def _told_so_far(sentences: list[dict], until: float) -> str:
    text = " ".join((s.get("display") or s.get("speech") or "") for s in sorted(sentences, key=lambda s: s["start"]) if s["start"] <= until)
    return text[-TOLD_SO_FAR_CHARS:]


def valid(data, asked: set[str]) -> list[dict] | None:
    """The agent's verdicts in its order, only for photos it was asked about; None when the answer is not the schema."""
    if not isinstance(data, dict) or not isinstance(data.get("photos"), list):
        return None
    out, seen = [], set()
    for entry in data["photos"]:
        if isinstance(entry, dict) and entry.get("asset") in asked and entry["asset"] not in seen:
            seen.add(entry["asset"])
            out.append({"asset": entry["asset"], "ok": entry.get("ok") is True, "reason": str(entry.get("reason") or "")[:300]})
    return out


async def _ask(db: Session, case: Case, mention: FirstMention, person: dict, photos: list[Item], told: str) -> list[dict] | None:
    payload = {"person": {k: person.get(k) for k in ("key", "name", "type", "description") if person.get(k)},
               "first_mention": {"seconds": round(mention.start, 1), "sentence": mention.sentence}, "told_so_far": told,
               "photos": [{"asset": i.code, "shows": DS.depicts(i.asset), "setting": (DS.card_of(i.asset) or {}).get("setting"),
                           "kind": (DS.card_of(i.asset) or {}).get("kind"), "title": i.asset.title} for i in photos]}
    try:
        with track_run(db, case.id, "Main Visual Planner: person inspector", input_summary=mention.key) as run:
            data, res = await asyncio.wait_for(
                run_agent("mvp.person_inspector", get_generation_provider(), json.dumps(payload, ensure_ascii=False)),
                ai_config.mvp().call_timeout_s)
            stamp_run(run, res, "mvp_person_inspector")
    except (GenerationError, TimeoutError) as e:
        log.warning("person inspector failed for %s: %s", mention.key, type(e).__name__)
        return None
    return valid(data, {i.code for i in photos})


async def inspect(db: Session, case: Case, needs_plan: VisualPlan, language: str, sentences: list[dict], persons: list[dict],
                  items: dict[str, list[Item]], beat_at, graph_id: int | None, beat_order: dict[str, int]) -> list[dict]:
    """The table for this film: for each person named for the first time, the photos that may be shown there, best first. A photo the
    firewall blocks at that beat or that shows an injury is left out before the agent is asked; the agent judges the rest."""
    from app.documentary.drawing.portraits import INJURY

    mentions = first_mentions(sentences, persons, beat_at)
    by_key = {p["key"]: p for p in persons}
    money = BudgetService(db)
    gate = asyncio.Semaphore(CALLS_AT_ONCE)
    rows: list[dict] = []

    async def one(mention: FirstMention) -> None:
        pool = [i for i in candidates(items, mention.key)
                if not INJURY.search(DS.depicts(i.asset))
                and not (mention.beat_id and reveal_blocked(db, graph_id, beat_order, mention.beat_id, i.asset))]
        if not pool:
            return
        async with gate:
            snapshot = money.snapshot(case.id)
            if snapshot.spent_eur >= snapshot.cap_eur - 1e-9:
                return
            verdicts = await _ask(db, case, mention, by_key[mention.key], pool, _told_so_far(sentences, mention.end))
        if not verdicts:
            return
        by_code = {i.code: i for i in pool}
        for rank, v in enumerate((v for v in verdicts if v["ok"]), 1):
            if rank > MAX_HINTS:
                break
            rows.append({"entity_key": mention.key, "person_name": mention.name, "mention_start": mention.start,
                         "mention_text": mention.sentence, "asset_id": by_code[v["asset"]].asset.id, "asset": v["asset"],
                         "rank": rank, "reason": v["reason"]})

    await asyncio.gather(*(one(m) for m in mentions))
    save(db, case, needs_plan, language, rows)
    return sorted(rows, key=lambda r: (r["mention_start"], r["rank"]))


def save(db: Session, case: Case, needs_plan: VisualPlan, language: str, rows: list[dict]) -> None:
    """Replace the table of this film (case, language) with the inspector's answer."""
    db.query(PersonPhotoHint).filter_by(case_id=case.id, language=language).delete(synchronize_session="fetch")
    for row in rows:
        db.add(PersonPhotoHint(case_id=case.id, visual_plan_id=needs_plan.id, language=language, entity_key=row["entity_key"],
                               person_name=row["person_name"], mention_start=row["mention_start"], mention_text=row["mention_text"],
                               visual_asset_id=row["asset_id"], rank=row["rank"], reason=row["reason"]))
    db.commit()


def hints_of(db: Session, case_id: int, language: str) -> list[dict]:
    """The inspector's table for this film as the planner reads it: [{entity_key, person_name, mention_start, asset (code), rank, reason}]."""
    out = []
    for h in (db.query(PersonPhotoHint).filter_by(case_id=case_id, language=language)
              .order_by(PersonPhotoHint.mention_start, PersonPhotoHint.rank).all()):
        asset = db.get(VisualAsset, h.visual_asset_id)
        out.append({"entity_key": h.entity_key, "person_name": h.person_name, "mention_start": h.mention_start,
                    "mention_text": h.mention_text, "asset": asset.asset_code, "rank": h.rank, "reason": h.reason})
    return out
