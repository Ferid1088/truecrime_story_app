"""Step 8 - visual selection and direction. For every beat the narration is cut into slots at sentence
boundaries; approved items fill the slots (the scene-director agent chooses, deterministic rules validate
and fill what it leaves). The result is the declarative visual plan the production script compiles."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, MvpCandidate, MvpRequirement, RevealNode, VisualAsset, VisualPlan
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

PHOTO_MOTIONS = ("SLOW_PUSH", "PAN_LEFT", "SLOW_PULL", "PAN_RIGHT")
ROLE_RANK = {"evidence": 0, "context": 1, "illustration": 2}
Director = Callable[[Case, dict, list[dict], list[dict]], Awaitable[list[dict]]]


@dataclass
class Item:
    """An approved library asset usable in a beat."""

    asset: VisualAsset
    role: str
    entity: str | None
    rank: int

    @property
    def code(self) -> str:
        return self.asset.asset_code

    @property
    def clip_seconds(self) -> float | None:
        if self.asset.asset_type != "video":
            return None
        lo = float(self.asset.clip_start or 0.0)
        hi = self.asset.clip_end if self.asset.clip_end is not None else self.asset.duration_seconds
        return float(hi) - lo if hi is not None else None


def beat_windows(manifest: dict) -> list[dict]:
    """Each beat's slot of the timeline, exactly as the production script cuts it: from its own start
    (the first from 0) to the next beat's start (the last to the end), with its timed sentences."""
    spans = sorted(manifest["timeline"].get("beats") or [], key=lambda b: b["start"])
    duration = float(manifest.get("duration_seconds") or 0.0)
    sentences = manifest["timeline"].get("sentences") or []
    out = []
    for i, span in enumerate(spans):
        start = 0.0 if i == 0 else span["start"]
        end = spans[i + 1]["start"] if i + 1 < len(spans) else duration
        inside = [s for s in sentences if start - 0.01 <= s["start"] < end - 0.01]
        out.append({"beat_id": span["beat_id"], "start": start, "end": end, "sentences": inside})
    return out


def make_slots(window: dict) -> list[dict]:
    """Cut a beat into slots at sentence ends: no longer than max_still, near the target length, and
    never shorter than min_seconds (a short tail folds into the slot before it)."""
    cfg = ai_config.mvp().shots
    length = window["end"] - window["start"]
    if length <= 0.05:
        return []
    target = max(min(60.0 / cfg.target_shots_per_minute, cfg.max_still_seconds), cfg.min_seconds)
    marks = [min(max(s["end"], window["start"]), window["end"]) for s in window["sentences"]]
    slots, begin, first = [], window["start"], 0
    for index, mark in enumerate(marks):
        last = index == len(marks) - 1
        if (mark - begin >= target or last) and mark - begin > 0.0:
            slots.append({"from": first, "to": index, "start": begin, "end": window["end"] if last else mark})
            begin, first = mark, index + 1
    if not slots:
        slots = [{"from": 0, "to": max(len(marks) - 1, 0), "start": window["start"], "end": window["end"]}]
    slots[-1]["end"] = window["end"]
    # a slot over max_still is split in equal parts: the picture changes, the sentence continues
    split: list[dict] = []
    for slot in slots:
        dur = slot["end"] - slot["start"]
        parts = max(1, math.ceil(dur / cfg.max_still_seconds))
        for k in range(parts):
            a = slot["start"] + dur * k / parts
            b = slot["start"] + dur * (k + 1) / parts
            split.append({**slot, "start": a, "end": b})
    merged: list[dict] = []
    for slot in split:
        if merged and slot["end"] - slot["start"] < cfg.min_seconds:
            merged[-1]["end"] = slot["end"]
        else:
            merged.append(slot)
    return merged


def approved_items(db: Session, plan: VisualPlan) -> dict[str, list[Item]]:
    """beat_id -> approved items, evidence before context, best priority first, deduplicated."""
    priority = {"high": 0, "medium": 1, "low": 2}
    rows = (db.query(MvpCandidate, MvpRequirement)
            .join(MvpRequirement, MvpRequirement.id == MvpCandidate.requirement_id)
            .filter(MvpRequirement.visual_plan_id == plan.id, MvpCandidate.final_status == "approved")
            .all())
    out: dict[str, list[Item]] = {}
    seen: dict[str, set[int]] = {}
    for cand, req in sorted(rows, key=lambda x: (priority[x[1].priority], ROLE_RANK.get(x[0].role or "", 3))):
        asset = db.get(VisualAsset, cand.visual_asset_id)
        if asset is None or asset.local_path is None:
            continue
        if asset.id in seen.setdefault(req.beat_id, set()):
            continue
        seen[req.beat_id].add(asset.id)
        out.setdefault(req.beat_id, []).append(
            Item(asset, cand.role or "context", req.entity_key, priority[req.priority] * 3
                 + ROLE_RANK.get(cand.role or "", 3)))
    return out


def reveal_blocked(db: Session, graph_id: int | None, beat_order: dict[str, int], beat_id: str,
                   asset: VisualAsset) -> bool:
    """True when the asset would reveal a graph node before the beat that first reveals it."""
    if graph_id is None:
        return False
    facts = json.loads(asset.reveals_json or "[]")
    if not facts:
        return False
    nodes = {n.node_key: n.first_revealed_beat_id for n in db.query(RevealNode).filter(
        RevealNode.graph_id == graph_id, RevealNode.node_key.in_(facts))}
    return any(beat_order.get(first, 0) > beat_order[beat_id] for first in nodes.values())


def fits(item: Item, seconds: float) -> bool:
    length = item.clip_seconds
    return length is None or length >= seconds - 0.5


def baseline_assign(slots: list[dict], pool: list[Item], used: Counter, max_reuse: int,
                    extra: list[Item]) -> list[Item | None]:
    """Fill slots in order from the beat's pool (never twice in the beat, never past the film-wide reuse
    cap); when the beat's own pool runs out, related approved items of nearby beats within the cap."""
    taken: set[str] = set()
    chosen: list[Item | None] = []
    for slot in slots:
        seconds = slot["end"] - slot["start"]
        pick = None
        for candidate in pool + extra:
            if candidate.code in taken or used[candidate.code] >= max_reuse or not fits(candidate, seconds):
                continue
            pick = candidate
            break
        if pick is not None:
            taken.add(pick.code)
            used[pick.code] += 1
        chosen.append(pick)
    return chosen


def coalesce_slots(slots: list[dict], available: int, max_still: float) -> list[dict]:
    """When the beat has fewer approved items than slots, neighbouring slots are joined so each item is
    held a little longer instead of the tail of the beat going bare; no joined slot passes max_still."""
    if available <= 0 or len(slots) <= available:
        return slots
    total = slots[-1]["end"] - slots[0]["start"]
    groups = max(available, math.ceil(total / max_still))
    if groups >= len(slots):
        return slots
    per = total / groups
    out: list[dict] = []
    for slot in slots:
        if out and (out[-1]["end"] - out[-1]["start"]) < per - 1e-6 and (
                slot["end"] - out[-1]["start"]) <= max_still:
            out[-1] = {**out[-1], "to": slot["to"], "end": slot["end"]}
        else:
            out.append(dict(slot))
    return out


def hold_into_gaps(slots: list[dict], chosen: dict[int, Item], max_still: float) -> list[tuple[int, dict]]:
    """A slot with nothing honest to show is covered by holding the previous picture longer, but never past
    max_still and never a clip (it plays once). What cannot be covered stays a gap, reported as a shortage."""
    out: list[tuple[int, dict]] = []
    for n, slot in enumerate(slots):
        if n not in chosen and out:
            last_n, last = out[-1]
            held_item = chosen.get(last_n)
            if (held_item is not None and held_item.asset.asset_type != "video"
                    and slot["end"] - last["start"] <= max_still):
                out[-1] = (last_n, {**last, "end": slot["end"]})
                continue
        out.append((n, dict(slot)))
    return out


def validate_assignment(raw: list[dict], slots: list[dict], pool: list[Item]) -> dict[int, Item] | None:
    """The agent's choice, or None if any of it breaks a rule (unknown item, twice in the beat, a clip too
    short for its slot). Nulls are allowed: a slot may honestly stay empty for the baseline to fill."""
    by_code = {i.code: i for i in pool}
    used: set[str] = set()
    out: dict[int, Item] = {}
    for a in raw or []:
        if not isinstance(a, dict) or not isinstance(a.get("slot"), int) or not 0 <= a["slot"] < len(slots):
            return None
        if a.get("asset") is None:
            continue
        item = by_code.get(a["asset"])
        seconds = slots[a["slot"]]["end"] - slots[a["slot"]]["start"]
        if item is None or item.code in used or not fits(item, seconds):
            return None
        used.add(item.code)
        out[a["slot"]] = item
    return out


async def model_director(case: Case, beat: dict, slots_view: list[dict], pool_view: list[dict],
                         db: Session | None = None) -> list[dict]:
    payload = {"beat": beat, "slots": slots_view, "pool": pool_view,
               "rules": {"max_still_seconds": ai_config.mvp().shots.max_still_seconds}}
    try:
        with track_run(db, case.id, "Main Visual Planner: direct", input_summary=beat["beat_id"]) as run:
            data, res = await run_agent("mvp.direct", get_generation_provider(),
                                        json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, "mvp_scene_director")
    except GenerationError as e:
        log.warning("scene director failed for %s: %s", beat["beat_id"], e)
        return []
    return (data or {}).get("assignments") or []


QUOTE_SECONDS = 6.0
DATE_SHARE = 0.02


def add_text_cards(shots: list[dict], extras: dict, window_len: float) -> None:
    """The beat's date (laid over the picture when it is spoken) and its verified quote (a quote card at
    the end of a beat long enough to hold one). Both come from the validated needs, never from a model here."""
    if extras.get("date_text") and len(shots) >= 1:
        shots.insert(1, {"command": "SHOW_DATE", "share": DATE_SHARE, "motion": "NONE", "speed": 1.0,
                         "transition_in": "CROSSFADE",
                         "overlay": {"kind": "date", "text_en": extras["date_text"]},
                         "why": "the beat is anchored to this date"})
    quote = extras.get("quote")
    if quote and window_len >= 4 * QUOTE_SECONDS and shots:
        share = QUOTE_SECONDS / window_len
        last = shots[-1]
        if "asset_id" in last and last["share"] * window_len - QUOTE_SECONDS >= 4.0:
            last["share"] = round(last["share"] - share, 5)
        else:
            shots.pop()
        shots.append({"command": "SHOW_QUOTE", "share": share, "motion": "NONE", "speed": 1.0,
                      "transition_in": "CROSSFADE", "overlay": {"kind": "quote", "text_en": quote["text"]},
                      "why": "a verified quotation from the evidence"})


def _shot(slot: dict, item: Item | None, window_len: float, index: int, why: str) -> dict:
    share = (slot["end"] - slot["start"]) / window_len
    if item is None:
        return {"command": "BLACK_SCREEN", "share": share, "motion": "NONE", "speed": 1.0,
                "transition_in": "CROSSFADE", "gap": True, "why": "no approved visual for this moment"}
    video = item.asset.asset_type == "video"
    return {"command": "SHOW_CLIP" if video else "NEW_IMAGE", "share": share, "asset_id": item.code,
            "motion": "NONE" if video else PHOTO_MOTIONS[index % len(PHOTO_MOTIONS)], "speed": 1.0,
            "transition_in": "CROSSFADE", "role": item.role, "why": why}


async def direct_film(db: Session, case: Case, plan: VisualPlan, manifest: dict,
                      graph_id: int | None, beat_order: dict[str, int], beats: dict[str, dict],
                      director: Director | None = None, target: VisualPlan | None = None) -> dict:
    """Build the plan: beat by beat, agent first, rules always. `plan` holds the approved requirements;
    the shot list is stored on `target` (the language's own plan row; `plan` itself when not given)."""
    target = target or plan
    cfg = ai_config.mvp().shots
    items = approved_items(db, plan)
    used: Counter = Counter()
    out_beats, candidates, gaps, agent_used = [], {}, [], 0
    windows = beat_windows(manifest)
    extras = {b["beat_id"]: b for b in json.loads(plan.requirements_json or "{}").get("beats") or []}
    fingerprint = hashlib.sha256(json.dumps({
        "windows": [[w["beat_id"], round(w["start"], 2), round(w["end"], 2), len(w["sentences"])]
                    for w in windows],
        "items": {b: [[i.code, i.role, i.asset.sha256] for i in group] for b, group in items.items()},
        "graph": graph_id, "shots": cfg.model_dump(),
        "extras": {b: [e.get("date_text"), (e.get("quote") or {}).get("text")] for b, e in extras.items()}}, sort_keys=True).encode()).hexdigest()
    previous = json.loads(target.validation_json or "{}").get("plan") or {}
    if target.status == "planned" and previous.get("fingerprint") == fingerprint:
        return previous
    for window in windows:
        bid = window["beat_id"]
        slots = make_slots(window)
        if not slots:
            continue
        pool = [i for i in items.get(bid, []) if not reveal_blocked(db, graph_id, beat_order, bid, i.asset)]
        near = [i for other, group in items.items() if abs(beat_order.get(other, 99) - beat_order.get(bid, 0)) <= 2
                and other != bid for i in group
                if i.entity in {p.entity for p in pool} and not reveal_blocked(
                    db, graph_id, beat_order, bid, i.asset)]
        slots = coalesce_slots(slots, len(pool) + len(near), cfg.max_still_seconds)
        chosen: dict[int, Item] = {}
        if director is not None and pool:
            slots_view = [{"slot": n, "seconds": round(s["end"] - s["start"], 1),
                           "sentences": [x["speech"] for x in window["sentences"][s["from"]:s["to"] + 1]]}
                          for n, s in enumerate(slots)]
            pool_view = [{"asset": i.code, "kind": i.asset.asset_type, "role": i.role, "entity": i.entity,
                          "depicts": (i.asset.description or i.asset.title or "")[:160],
                          "clip_seconds": i.clip_seconds, "shown_before": used[i.code]} for i in pool]
            proposal = validate_assignment(await director(case, beats.get(bid, {"beat_id": bid}),
                                                          slots_view, pool_view), slots, pool)
            if proposal:
                chosen, agent_used = proposal, agent_used + 1
                for item in proposal.values():
                    used[item.code] += 1
        leftover = [n for n in range(len(slots)) if n not in chosen]
        taken = {i.code for i in chosen.values()}
        fill = baseline_assign([slots[n] for n in leftover], [i for i in pool if i.code not in taken], used,
                               cfg.max_asset_reuse, [i for i in near if i.code not in taken])
        for n, item in zip(leftover, fill, strict=True):
            if item is not None:
                chosen[n] = item
        window_len = window["end"] - window["start"]
        held = hold_into_gaps(slots, chosen, cfg.max_still_seconds)
        shots = [_shot(s, chosen.get(n), window_len, n, "approved item for this moment")
                 for n, s in held]
        for n, s in held:
            if n not in chosen:
                gaps.append({"beat": bid, "start": round(s["start"], 2), "end": round(s["end"], 2)})
        add_text_cards(shots, extras.get(bid) or {}, window_len)
        total = sum(s["share"] for s in shots)
        for s in shots:
            s["share"] = round(s["share"] / total, 5)
        out_beats.append({"beat_id": bid, "shots": shots})
        candidates[bid] = sorted({i.code for i in pool + near})
    report = {"fingerprint": fingerprint, "beats": len(out_beats), "shots": sum(len(b["shots"]) for b in out_beats),
              "gaps": gaps, "gap_seconds": round(sum(g["end"] - g["start"] for g in gaps), 1),
              "agent_directed_beats": agent_used, "assets_used": len(used),
              "reuse": {k: v for k, v in used.items() if v > 1}}
    target.plan_json = json.dumps({"beats": out_beats, "candidates": candidates, "mvp": True},
                                  ensure_ascii=False)
    target.status = "planned"
    validation = json.loads(target.validation_json or "{}")
    target.validation_json = json.dumps({**validation, "plan": {"status": "valid" if not gaps else "needs_review",
                                                               **report}}, ensure_ascii=False)
    db.commit()
    return report
