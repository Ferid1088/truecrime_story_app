"""Illustrations for the moments no real picture covers (Fix: no long stretches of cards or black).

An illustration is a generated picture in a clearly drawn style (an engraving), never a photograph
and never a fabricated image of a real person or of an event as it was: the brief asks for
settings and objects only, the asset is a "generated" illustration (role "illustration", rights
"owned"), and the film shows the label "Illustration" for as long as it is on screen (the existing
label rule). Each one carries the entities of its beat so the visual director offers it for that
beat; a picture shows only once (the usage policy), so each beat gets as many as it has picture slots."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import uuid

import httpx
from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.env import env_value
from app.db.models import Case, EditorialBlueprint, VisualAsset
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import tiers as T
from app.documentary.visuals.research import next_asset_code
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

PROVIDER = "generated"
KIND = "illustration"


class IllustrationError(RuntimeError):
    pass


def spec_key(brief: str, beat_id: str, index: int) -> str:
    return "illustration|" + hashlib.sha256(f"{beat_id}|{index}|{brief}".encode()).hexdigest()[:20]


def image_prompt(brief: str) -> str:
    cfg = ai_config.illustrations
    return (f"An illustration, {cfg.style}. Subject: {brief}. {cfg.forbidden}. "
            "It is clearly a drawing, not a photograph.")


async def generate_png(prompt: str) -> bytes:
    """One image from the gateway's /images/generations (b64_json or a url)."""
    cfg = ai_config.illustrations
    gen = ai_config.generation_provider()
    base = gen.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {env_value(gen.secret_env) or ''}"}
    body = {"model": cfg.image_model, "prompt": prompt, "n": 1, "size": cfg.size,
            "quality": cfg.quality}
    async with httpx.AsyncClient(timeout=cfg.timeout_s) as client:
        r = await client.post(f"{base}/images/generations", headers=headers, json=body)
        r.raise_for_status()
        item = (r.json().get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            got = await client.get(item["url"])
            got.raise_for_status()
            return got.content
    raise IllustrationError("the image model returned neither b64_json nor a url")


def beat_seconds(beat: dict) -> float:
    from app.documentary.visuals.director import beat_seconds as _seconds

    return _seconds(beat)


def counts_per_beat(blueprint: dict) -> dict[str, int]:
    """How many illustrations each beat gets: one per seconds_per_picture of the beat, the whole
    film capped at max_per_film (spread by trimming the longest beats first)."""
    cfg = ai_config.illustrations
    counts = {b["id"]: max(1, math.ceil(beat_seconds(b) / cfg.seconds_per_picture))
              for b in blueprint.get("beats") or []}
    while sum(counts.values()) > cfg.max_per_film and counts:
        longest = max(counts, key=lambda k: counts[k])
        counts[longest] -= 1
    return counts


def existing(db: Session, case_id: int) -> dict[str, list[VisualAsset]]:
    """The case's illustrations by beat."""
    out: dict[str, list[VisualAsset]] = {}
    for a in db.query(VisualAsset).filter(VisualAsset.case_id == case_id,
                                          VisualAsset.provider == PROVIDER,
                                          VisualAsset.asset_role == "illustration").all():
        spec = json.loads(a.spec_json or "{}")
        if spec.get("kind") == KIND:
            out.setdefault(spec.get("beat_id") or "", []).append(a)
    return out


async def briefs(db: Session, case: Case, blueprint: dict, requirements: dict,
                 wanted: dict[str, int]) -> dict[str, list[dict]]:
    """The briefs per beat from the planner role; a beat the model skipped gets generic setting
    briefs from its own summary so the film never lacks pictures."""
    entities = {e["key"]: e for e in requirements.get("entities") or []}
    req_by_beat = {b["beat_id"]: b for b in requirements.get("beats") or []}
    beats = []
    for b in blueprint.get("beats") or []:
        n = wanted.get(b["id"], 0)
        if not n:
            continue
        keys = [r["entity"] for r in (req_by_beat.get(b["id"]) or {}).get("requirements") or []]
        beats.append({
            "beat_id": b["id"], "purpose": b.get("purpose"), "summary": b.get("summary"),
            "visual_intent": b.get("visual_intent"), "seconds": round(beat_seconds(b)),
            "entities": [{"key": k, "name": entities[k]["name"], "type": entities[k]["type"]}
                         for k in dict.fromkeys(keys) if k in entities],
            "count": n})
    payload = {"case": case.canonical_title, "place": case.location,
               "period": str(case.incident_date or ""), "beats": beats}
    gen = get_generation_provider()
    with track_run(db, case.id, "Illustration Briefs", input_summary=f"beats={len(beats)}") as run:
        raw, res = await run_agent("visuals.illustration_briefs", gen,
                                   json.dumps(payload, ensure_ascii=False))
        stamp_run(run, res, "visual_planner")
    out: dict[str, list[dict]] = {}
    for item in (raw or {}).get("beats") or []:
        good = [x for x in item.get("briefs") or []
                if isinstance(x, dict) and str(x.get("subject") or "").strip()]
        out[str(item.get("beat_id"))] = good
    for b in beats:
        have = out.setdefault(b["beat_id"], [])
        while len(have) < b["count"]:
            have.append({"subject": f"a quiet interior or street of the period and place, "
                                    f"mood of: {str(b['summary'] or '')[:120]}", "entities": []})
        del have[b["count"]:]
    return out


def book(db: Session, case_id: int, units: int) -> None:
    from app.services.budget import BudgetService
    from app.services.cost_model import load_pricing

    price = float(load_pricing()["images"]["eur_per_image"])
    BudgetService(db).record(case_id=case_id, stage="visual_illustration",
                             model=ai_config.illustrations.image_model, units=float(units),
                             cost_eur=round(price * units, 6), estimated=False,
                             request_id=f"illustrations:{case_id}:{uuid.uuid4().hex[:10]}",
                             cost_source="measured")


def make_asset(db: Session, case: Case, png: bytes, brief: dict, beat_id: str, index: int,
               entity_keys: list[str]) -> VisualAsset:
    img = IM.open_image(png)
    w, h = img.size
    if w / h < 16 / 9 - 0.01:                      # crop to 16:9 from the middle
        target = int(w * 9 / 16)
        top = (h - target) // 2
        img = img.crop((0, top, w, top + target))
    code = next_asset_code(db)
    out = storage.visuals_dir(case.id, "illustration") / f"{code}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=92)
    thumb = storage.thumbs_dir(case.id) / f"{code}.jpg"
    IM.save_thumbnail(img.convert("RGB"), thumb)
    tier, why = T.provisional_why(PROVIDER, "illustration", None)
    a = VisualAsset(
        case_id=case.id, asset_code=code, asset_type="photo", subject_type="other",
        title=f"Illustration: {brief['subject']}"[:500], description=brief["subject"][:1000],
        provider=PROVIDER, asset_role="illustration",
        rights_status="owned", rights_reason="illustration generated by the app (labelled on screen)",
        verification_status="verified", verification_confidence=1.0,
        local_path=storage.rel(out), thumbnail_path=storage.rel(thumb),
        width=img.width, height=img.height, quality_score=0.7,
        relevance_tier=max(tier, 5), case_relevance=T.tier_label(max(tier, 5)),
        entity_key=(entity_keys[0] if entity_keys else None), entities_json=json.dumps(entity_keys),
        found_during="generated", sha256=IM.sha256(png),
        spec_json=json.dumps({"kind": KIND, "key": spec_key(brief["subject"], beat_id, index),
                              "beat_id": beat_id, "index": index, "brief": brief["subject"],
                              "model": ai_config.illustrations.image_model, "tier_reason": why}))
    db.add(a)
    db.commit()
    return a


async def ensure_pool(db: Session, case: Case, bp_row: EditorialBlueprint, requirements: dict) -> dict:
    """Make the illustrations the film's beats need (those that exist are kept): returns counts."""
    cfg = ai_config.illustrations
    if not cfg.enabled:
        return {"skipped": True, "reason": "illustrations are off"}
    blueprint = json.loads(bp_row.blueprint_json or "{}")
    wanted = counts_per_beat(blueprint)
    have = existing(db, case.id)
    missing = {b: max(0, n - len(have.get(b, []))) for b, n in wanted.items()}
    missing = {b: n for b, n in missing.items() if n}
    if not missing:
        return {"existing": sum(len(v) for v in have.values()), "made": 0}
    plan = await briefs(db, case, blueprint, requirements, missing)
    entity_keys = {e["key"] for e in requirements.get("entities") or []}
    jobs = [(beat, i + len(have.get(beat, [])), brief)
            for beat, items in plan.items() for i, brief in enumerate(items)]
    sem = asyncio.Semaphore(cfg.concurrency)
    errors = 0

    async def one(beat: str, index: int, brief: dict):
        nonlocal errors
        async with sem:
            try:
                return beat, index, brief, await generate_png(image_prompt(brief["subject"]))
            except (httpx.HTTPError, IllustrationError):
                errors += 1
                return None

    results = [r for r in await asyncio.gather(*(one(*j) for j in jobs)) if r]
    made = 0
    for beat, index, brief, png in results:
        own = [k for k in (brief.get("entities") or []) if k in entity_keys]
        keys = list(dict.fromkeys(own + _beat_entities(requirements, beat)))
        make_asset(db, case, png, brief, beat, index, keys)
        made += 1
    if made:
        book(db, case.id, made)
    out = {"existing": sum(len(v) for v in have.values()), "made": made}
    if errors:
        out["degraded"] = f"{errors} illustration(s) could not be made and are retried next run"
    return out


def _beat_entities(requirements: dict, beat_id: str) -> list[str]:
    for b in requirements.get("beats") or []:
        if b["beat_id"] == beat_id:
            return list(dict.fromkeys(r["entity"] for r in b.get("requirements") or []))
    return []


def beat_of(a: VisualAsset) -> str:
    return json.loads(a.spec_json or "{}").get("beat_id") or ""


async def more_for_beats(db: Session, case: Case, bp_row: EditorialBlueprint, requirements: dict,
                         counts: dict[str, int]) -> list[VisualAsset]:
    """More illustrations for beats whose pictures ran out during the coverage repair."""
    counts = {b: n for b, n in counts.items() if n > 0 and b}
    if not counts or not ai_config.illustrations.enabled:
        return []
    blueprint = json.loads(bp_row.blueprint_json or "{}")
    have = existing(db, case.id)
    plan = await briefs(db, case, blueprint, requirements, counts)
    entity_keys = {e["key"] for e in requirements.get("entities") or []}
    sem = asyncio.Semaphore(ai_config.illustrations.concurrency)

    async def one(beat: str, index: int, brief: dict):
        async with sem:
            try:
                return beat, index, brief, await generate_png(image_prompt(brief["subject"]))
            except (httpx.HTTPError, IllustrationError):
                return None

    jobs = [(beat, i + len(have.get(beat, [])), brief)
            for beat, items in plan.items() for i, brief in enumerate(items)]
    made: list[VisualAsset] = []
    for r in await asyncio.gather(*(one(*j) for j in jobs)):
        if r is None:
            continue
        beat, index, brief, png = r
        own = [k for k in (brief.get("entities") or []) if k in entity_keys]
        keys = list(dict.fromkeys(own + _beat_entities(requirements, beat)))
        made.append(make_asset(db, case, png, brief, beat, index, keys))
    if made:
        book(db, case.id, len(made))
    return made


async def slot_brief(db: Session, case: Case, sentences: list[str], beat_summary: str | None,
                     avoid: list[str]) -> str:
    """The brief for the exact words spoken over one gap; `avoid` carries the auditor's reasons."""
    payload = {"case": case.canonical_title, "place": case.location,
               "period": str(case.incident_date or ""), "beat_summary": beat_summary,
               "narration": sentences, "avoid": avoid[-6:]}
    gen = get_generation_provider()
    with track_run(db, case.id, "Illustration Briefs", input_summary="slot") as run:
        raw, res = await run_agent("visuals.slot_brief", gen, json.dumps(payload, ensure_ascii=False))
        stamp_run(run, res, "visual_planner")
    subject = str((raw or {}).get("subject") or "").strip()
    if not subject:
        raise IllustrationError("the planner returned no illustration brief for the gap")
    return subject
