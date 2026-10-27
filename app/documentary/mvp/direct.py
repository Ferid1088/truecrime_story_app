"""Step 8 - visual selection and direction. For every beat the narration is cut into slots at sentence
boundaries; approved items fill the slots (the scene-director agent chooses, deterministic rules validate
and fill what it leaves). The result is the declarative visual plan the production script compiles."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, MvpCandidate, MvpRequirement, RevealNode, VisualAsset, VisualPlan
from app.documentary.mvp import alternatives as AL
from app.documentary.mvp.needs import fold
from app.documentary.visuals.usage import UsageTracker, asset_facts
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
    serves: list[str] = field(default_factory=list)      # requirement keys this item is approved for

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
    for cand, req in sorted(rows, key=lambda x: (priority[x[1].priority], ROLE_RANK.get(x[0].role or "", 3))):
        asset = db.get(VisualAsset, cand.visual_asset_id)
        if asset is None or asset.local_path is None:
            continue
        existing = next((i for i in out.get(req.beat_id, []) if i.asset.id == asset.id), None)
        if existing is not None:
            existing.serves.append(req.requirement_key)
            continue
        out.setdefault(req.beat_id, []).append(
            Item(asset, cand.role or "context", req.entity_key, priority[req.priority] * 3
                 + ROLE_RANK.get(cand.role or "", 3), [req.requirement_key]))
    return out


def requirement_first(pool: list[Item]) -> list[Item]:
    """The beat's items ordered so every requirement gets one of its pictures before any requirement
    gets a second: the best item per requirement (most important requirement first), then the rest."""
    first: list[Item] = []
    seen: set[str] = set()
    for item in sorted(pool, key=lambda i: i.rank):
        keys = [k for k in item.serves if k not in seen]
        if keys:
            first.append(item)
            seen.update(item.serves)
    return first + [i for i in sorted(pool, key=lambda i: i.rank) if i not in first]



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


STOP = {"the", "and", "that", "was", "were", "his", "her", "had", "has", "with", "for", "from", "this",
        "they", "them", "she", "him", "but", "not", "are", "who", "when", "then", "than", "into", "over",
        "after", "before", "said", "would", "could", "which", "their", "there", "been", "also", "about"}


def content_words(text: str) -> set[str]:
    return {w for w in fold(text or "").split() if len(w) > 2 and w not in STOP}


def related(item: Item, sentence_text: str, names: dict[str, set[str]]) -> bool:
    """Whether the words spoken over a slot are about what the picture shows: the entity the picture was
    approved for is named in them, or at least two content words of its verified description are."""
    spoken = content_words(sentence_text)
    if item.entity and names.get(item.entity, set()) & spoken:
        return True
    return len(content_words(item.asset.description or "") & spoken) >= 2


def fits(item: Item, seconds: float) -> bool:
    length = item.clip_seconds
    return length is None or length >= seconds - 0.5


def baseline_assign(slots: list[dict], pool: list[Item], tracker: UsageTracker,
                    extra: list[Item], about: Callable[[Item, dict], bool] | None = None) -> list[Item | None]:
    """Fill slots in order from the beat's pool, then from related approved items. An item is taken only if
    the words over the slot are about it (`about`) and the film's repetition rules (category limits, minimum
    gap between appearances) allow it there; otherwise the slot stays empty for a factual graphic."""
    taken: set[str] = set()
    chosen: list[Item | None] = []
    for slot in slots:
        seconds = slot["end"] - slot["start"]
        pick = next((c for c in pool + extra if c.code not in taken and fits(c, seconds)
                     and (about is None or about(c, slot))
                     and tracker.allows(c.code, slot["start"], slot["end"])[0]), None)
        if pick is not None:
            taken.add(pick.code)
            tracker.add(pick.code, slot["start"], slot["end"])
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
            data, res = await asyncio.wait_for(
                run_agent("mvp.direct", get_generation_provider(), json.dumps(payload, ensure_ascii=False)),
                ai_config.mvp().call_timeout_s)
            stamp_run(run, res, "mvp_scene_director")
    except (GenerationError, TimeoutError) as e:
        log.warning("scene director failed for %s: %s", beat["beat_id"], e)
        return []
    return (data or {}).get("assignments") or []


DATE_SHARE = 0.02


def add_date_overlay(shots: list[dict], extras: dict) -> None:
    """The beat's date, laid over the picture when it is spoken (from the needs plan, never from a model here)."""
    if extras.get("date_text") and len(shots) >= 1:
        shots.insert(1, {"command": "SHOW_DATE", "share": DATE_SHARE, "motion": "NONE", "speed": 1.0,
                         "transition_in": "CROSSFADE",
                         "overlay": {"kind": "date", "text_en": extras["date_text"]},
                         "why": "the beat is anchored to this date"})


def _take_gap(shots: list[dict], window_len: float, minimum: float) -> dict | None:
    """The longest bare stretch of the beat that can hold a card of at least `minimum` seconds."""
    gaps = [s for s in shots if s.get("gap") and s["share"] * window_len >= minimum]
    return max(gaps, key=lambda s: s["share"]) if gaps else None


def map_seconds() -> float:
    """What a first-mention zoom map needs on screen: the camera move and the hold on the place."""
    variant = ai_config.zoom().variants[ai_config.zoom().variant_rules["first"]]
    return variant.seconds + variant.hold_seconds


def add_map_cards(shots: list[dict], extras: dict, window_len: float, places: list[str],
                  mapped: set[str], region: str | None) -> None:
    """A map only where the narration names its place (the bare stretch whose words say it), each place once in
    the film, geocoded with the case's region so a common name resolves near the case. The map gets the time its zoom
    needs (the move and the hold on the place) so the viewer sees where it is; a stretch that cannot give it stays bare."""
    wanted = ([extras["map_place"]] if extras.get("map_place") else []) + places
    for place in wanted:
        if not place or fold(place) in mapped:
            continue
        words = content_words(place)
        need = map_seconds()
        run = next((r for r in AL.gap_runs(shots, window_len) if r[2] >= 0.8 * need and words
                    and words <= content_words(" ".join(s.get("said", "") for s in shots[r[0]:r[1] + 1]))), None)
        if run is None:
            continue
        first, last, seconds = run
        said = " ".join(s.get("said", "") for s in shots[first:last + 1]).strip()
        length = min(need, seconds)
        replacement = [{"command": "SHOW_MAP", "map_place": f"{place}, {region}" if region and "," not in place
                        else place, "share": length / window_len, "motion": "NONE", "speed": 1.0,
                        "transition_in": "CROSSFADE", "said": said,
                        "why": "the narration names this place; a map shows where it is"}]
        if seconds - length > 1e-6:
            replacement.append({"command": "BLACK_SCREEN", "share": (seconds - length) / window_len, "motion": "NONE",
                                "speed": 1.0, "transition_in": "CROSSFADE", "gap": True, "said": said,
                                "why": "no approved visual for this moment"})
        shots[first:last + 1] = replacement
        mapped.add(fold(place))


def absorb_gaps(shots: list[dict], window_len: float) -> None:
    """The last resort before black: hold the picture next to a bare stretch longer (never a clip, never
    past max_hold_seconds). What cannot be absorbed stays bare and is reported."""
    limit = ai_config.mvp().shots.max_still_seconds
    for i, shot in enumerate(list(shots)):
        if not shot.get("gap"):
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(shots):
                other = shots[j]
                if (other.get("command") == "NEW_IMAGE" and not other.get("gap")
                        and (other["share"] + shot["share"]) * window_len <= limit + 1e-6):
                    other["share"] += shot["share"]
                    shot["share"] = 0.0
                    break
    shots[:] = [s for s in shots if s["share"] > 1e-9]


def _shot(slot: dict, item: Item | None, window_len: float, index: int, why: str, said: str = "") -> dict:
    share = (slot["end"] - slot["start"]) / window_len
    if item is None:
        return {"command": "BLACK_SCREEN", "share": share, "motion": "NONE", "speed": 1.0,
                "transition_in": "CROSSFADE", "gap": True, "said": said,
                "why": "no approved visual for this moment"}
    video = item.asset.asset_type == "video"
    return {"command": "SHOW_CLIP" if video else "NEW_IMAGE", "share": share, "asset_id": item.code,
            "motion": "NONE" if video else PHOTO_MOTIONS[index % len(PHOTO_MOTIONS)], "speed": 1.0,
            "transition_in": "CROSSFADE", "role": item.role, "why": why}


async def direct_film(db: Session, case: Case, plan: VisualPlan, manifest: dict,
                      graph_id: int | None, beat_order: dict[str, int], beats: dict[str, dict],
                      director: Director | None = None, target: VisualPlan | None = None,
                      entities: list[dict] | None = None, region: str | None = None,
                      offers: list[AL.Offer] | None = None) -> dict:
    """Build the plan: beat by beat, agent first, rules always. `plan` holds the approved requirements;
    the shot list is stored on `target` (the language's own plan row; `plan` itself when not given)."""
    target = target or plan
    offers = offers or []
    for o in offers:
        o.placed = False
    state = AL.State()
    entities = entities or json.loads(plan.requirements_json or "{}").get("entities") or []
    names = {e["key"]: content_words(" ".join([e["name"], *e.get("aliases", [])])) for e in entities}
    place_names = {e["key"]: e["name"] for e in entities if e.get("type") in ("place", "building")}
    mapped: set[str] = set()
    cfg = ai_config.mvp().shots
    items = approved_items(db, plan)
    tracker = UsageTracker({i.code: asset_facts(i.asset) for group in items.values() for i in group})
    out_beats, candidates, gaps, agent_used = [], {}, [], 0
    windows = beat_windows(manifest)
    extras = {b["beat_id"]: b for b in json.loads(plan.requirements_json or "{}").get("beats") or []}
    fingerprint = hashlib.sha256(json.dumps({
        "windows": [[w["beat_id"], round(w["start"], 2), round(w["end"], 2), len(w["sentences"])]
                    for w in windows],
        "items": {b: [[i.code, i.role, i.asset.sha256] for i in group] for b, group in items.items()},
        "graph": graph_id, "shots": cfg.model_dump(),
        "extras": {b: [e.get("date_text"), (e.get("quote") or {}).get("text")] for b, e in extras.items()},
        "places": sorted(place_names), "region": region,
        "offers": [[o.key, o.first, o.last, o.seconds] for o in offers]}, sort_keys=True).encode()).hexdigest()
    previous = json.loads(target.validation_json or "{}").get("plan") or {}
    if target.status == "planned" and previous.get("fingerprint") == fingerprint:
        return previous
    beat_cache: dict = previous.get("beat_cache") or {}
    new_cache: dict = {}
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
        pool_key = hashlib.sha256(json.dumps(
            [[i.code, i.role, tracker.appearances(i.code)] for i in pool] + [[s_["start"], s_["end"]] for s_ in slots],
            sort_keys=True).encode()).hexdigest()
        cached = beat_cache.get(bid)
        if cached and cached["key"] == pool_key:
            by_code = {i.code: i for i in pool}
            chosen = {int(n): by_code[c] for n, c in cached["assignment"].items() if c in by_code}
            for n, item in chosen.items():
                tracker.add(item.code, slots[n]["start"], slots[n]["end"])
        elif director is not None and pool:
            slots_view = [{"slot": n, "seconds": round(s["end"] - s["start"], 1),
                           "sentences": [x["speech"] for x in window["sentences"][s["from"]:s["to"] + 1]]}
                          for n, s in enumerate(slots)]
            pool_view = [{"asset": i.code, "kind": i.asset.asset_type, "role": i.role, "entity": i.entity,
                          "depicts": (i.asset.description or i.asset.title or "")[:160],
                          "clip_seconds": i.clip_seconds, "shown_before": tracker.appearances(i.code), "serves": i.serves} for i in pool]
            proposal = validate_assignment(await director(case, beats.get(bid, {"beat_id": bid}),
                                                          slots_view, pool_view), slots, pool)
            if proposal:
                for n, item in sorted(proposal.items()):
                    if tracker.allows(item.code, slots[n]["start"], slots[n]["end"])[0]:
                        tracker.add(item.code, slots[n]["start"], slots[n]["end"])
                        chosen[n] = item
                agent_used += 1 if chosen else 0
        if not (cached and cached["key"] == pool_key):
            new_cache[bid] = {"key": pool_key, "assignment": {str(n): i.code for n, i in chosen.items()}}
        else:
            new_cache[bid] = cached
        leftover = [n for n in range(len(slots)) if n not in chosen]
        taken = {i.code for i in chosen.values()}
        slot_text = {id(sl): " ".join(x["speech"] for x in window["sentences"][sl["from"]:sl["to"] + 1])
                     for sl in slots}
        fill = baseline_assign([slots[n] for n in leftover],
                               requirement_first([i for i in pool if i.code not in taken]), tracker,
                               [i for i in near if i.code not in taken],
                               lambda item, sl: related(item, slot_text.get(id(sl), ""), names))
        for n, item in zip(leftover, fill, strict=True):
            if item is not None:
                chosen[n] = item
        window_len = window["end"] - window["start"]
        shots = [_shot(s, chosen.get(n), window_len, n, "approved item for this moment",
                       slot_text.get(id(s), "")) for n, s in enumerate(slots)]
        spoken = " ".join(x["speech"] for x in window["sentences"])
        places = [name for key, name in place_names.items() if content_words(name) <= content_words(spoken)]
        add_map_cards(shots, extras.get(bid) or {}, window_len, places, mapped, region)
        add_date_overlay(shots, extras.get(bid) or {})
        idx = beat_order.get(bid, 0)
        AL.fill_beat(shots, window_len, idx, offers, state)
        board = AL.board_offer(state, idx + 1) if AL.board_due(offers, state, idx) else None
        if board is not None:
            offers.append(board)
        absorb_gaps(shots, window_len)
        total = sum(s["share"] for s in shots)
        for s in shots:
            s["share"] = round(s["share"] / total, 5)
        at = window["start"]
        for s in shots:
            if s.get("gap"):
                gaps.append({"beat": bid, "start": round(at, 2), "end": round(at + s["share"] * window_len, 2),
                             "said": (s.get("said") or "")[:240]})
            at += s["share"] * window_len
        out_beats.append({"beat_id": bid, "shots": shots})
        candidates[bid] = sorted({i.code for i in pool + near})
    report = {"fingerprint": fingerprint, "beat_cache": new_cache, "beats": len(out_beats), "shots": sum(len(b["shots"]) for b in out_beats),
              "gaps": gaps, "gap_seconds": round(sum(g["end"] - g["start"] for g in gaps), 1),
              "agent_directed_beats": agent_used, "alternatives": AL.seconds_report(state),
              "alternatives_unplaced": sorted(o.key for o in offers if not o.placed), "assets_used": len({c for c in tracker.facts if tracker.appearances(c)}),
              "reuse": {c: tracker.appearances(c) for c in tracker.facts if tracker.appearances(c) > 1}}
    target.plan_json = json.dumps({"beats": out_beats, "candidates": candidates, "mvp": True},
                                  ensure_ascii=False)
    target.status = "planned"
    validation = json.loads(target.validation_json or "{}")
    target.validation_json = json.dumps({**validation, "plan": {"status": "valid" if not gaps else "needs_review",
                                                               **report}}, ensure_ascii=False)
    db.commit()
    return report
