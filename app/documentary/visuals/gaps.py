"""Production-time media search (Master task §9.2): once the plan exists we
know WHICH sentence needs WHAT, so we search for exactly that instead of
filling the gap with a picture the film already showed.

Weak sentences (deterministic):
  * the director asked for it: REQUEST_SEARCH {sentence, entity,
    queries, why} (plan["search_requests"]);
  * a sentence names an entity that has no usable picture of tier <=
    visual_direction.weak_tier (the actual church, not "a church");
  * a shot fell back to holding the picture or to black, or repeats a
    picture beyond what its category allows (a generic/contextual one
    at all).

Requests: one per entity (the first weak sentence that names it), at
most visual_direction.max_search_requests, each with
queries_per_request queries — the director's own queries, then the
entity's name with the place of the case ("St Mary's Church Stendal"),
its exterior / historical photograph variants (buildings and places),
then the planner's context queries.

Then: VisualResearchAgent.run(found_during="production_search") ->
vision check of the new assets -> ONLY the beats of the requests are
directed again with the larger candidate lists (the rest of the plan
stays) -> maps and document cards re-materialized. The audit goes to the
plan's validation_json["search_requests"]: sentence, entity, why,
queries, assets_found (codes), used.

Bounded and skippable: visual_direction.production_search false skips
the stage; a pilot only looks at the beats it shows.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import AudioPlan, Case, EditorialBlueprint, VisualAsset, VisualPlan
from app.documentary.visuals.director import blocked_at, entity_score, usable
from app.documentary.visuals.planner import FOOTAGE_TYPES, fold, query_item
from app.documentary.visuals.usage import (
    CONTEXT, GENERIC, MAP, asset_facts, asset_tier, category, limit_of,
)

log = logging.getLogger(__name__)

# New assets vision-checked per request (the first ones found).
VERIFY_PER_REQUEST = 3
# A library picture "is" the entity only when it was found for it or its
# text names it fully (entity_score 0.7+), not when one word matches.
MIN_ENTITY_SCORE = 0.7
# Request sources in the order they are served when the budget is short.
SOURCE_ORDER = {"director": 0, "entity_gap": 1, "fallback": 2, "repeat": 3}


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# weak sentences (pure)
# ---------------------------------------------------------------------------


def _sentence(pb: dict, n: int | None) -> dict:
    sents = pb.get("sentences") or []
    if not sents:
        return {"n": 0, "text": None, "entities": []}
    n = min(max(int(n or 0), 0), len(sents) - 1)
    return sents[n]


def entity_gaps(pb: dict, entities: dict[str, dict], assets: list[VisualAsset],
                blocked: set[str], profile: str | None, weak_tier: int) -> list[dict]:
    """Sentences of a plan beat that name an entity without any usable
    picture of it at tier <= weak_tier."""
    out, best = [], {}
    for sn in pb.get("sentences") or []:
        for key in sn.get("entities") or []:
            ent = entities.get(key)
            if ent is None:
                continue
            if key not in best:
                tiers = [asset_tier(a) for a in assets
                         if usable(a, profile) and a.asset_type not in ("map",)
                         and a.provider != "generated"
                         and not (set(_loads(a.reveals_json, [])) & blocked)
                         and entity_score(a, ent) >= MIN_ENTITY_SCORE]
                best[key] = min(tiers) if tiers else None
            if best[key] is None or best[key] > weak_tier:
                out.append({
                    "beat_id": pb["beat_id"], "from_sentence": sn["n"], "sentence": sn.get("text"),
                    "entity": key, "entity_name": ent["name"], "queries": [],
                    "why": (f"names {ent['name']} but the library has no usable picture of it"
                            if best[key] is None else
                            f"names {ent['name']} but its best picture is tier {best[key]} "
                            f"(weak above tier {weak_tier})"),
                    "source": "entity_gap"})
    return out


def shot_gaps(pb: dict, assets_by_code: dict[str, VisualAsset], beat_req: dict) -> list[dict]:
    """Sentences where the plan holds, goes black or repeats a picture it
    should not."""
    out = []
    first_req = next((r["entity"] for r in beat_req.get("requirements") or []), None)
    for s in pb.get("shots") or []:
        why, source = None, None
        if str(s.get("why") or "").startswith("fallback:") and s.get("command") in (
                "KEEP_CURRENT_IMAGE", "BLACK_SCREEN"):
            why = ("nothing fits: the plan holds the previous picture"
                   if s["command"] == "KEEP_CURRENT_IMAGE" else "nothing fits: black screen")
            source = "fallback"
        elif s.get("repeat") and s.get("asset_id") in assets_by_code:
            facts = asset_facts(assets_by_code[s["asset_id"]])
            cat = category(facts)
            if cat in (GENERIC, CONTEXT, MAP) or int(s["repeat"]) >= limit_of(cat):
                why = f"repeats {s['asset_id']} ({cat}, already used {s['repeat']}x)"
                source = "repeat"
        if why is None:
            continue
        sn = _sentence(pb, s.get("from_sentence"))
        entity = (sn.get("entities") or [None])[0] or first_req
        if not entity:
            continue
        out.append({"beat_id": pb["beat_id"], "from_sentence": sn.get("n", 0),
                    "sentence": sn.get("text"), "entity": entity, "queries": [],
                    "why": why, "source": source})
    return out


def weak_sentences(plan: dict, requirements: dict, assets: list[VisualAsset],
                   blueprint: dict, beat_ids: list[str] | None = None,
                   profile: str | None = None) -> list[dict]:
    """Every weak sentence of the plan (in the given beats), director
    requests first, then entity gaps, then fallbacks and repeats — each
    group in film order."""
    weak_tier = ai_config.visual_direction.weak_tier
    entities = {e["key"]: e for e in requirements.get("entities") or []}
    reqs = {b["beat_id"]: b for b in requirements.get("beats") or []}
    by_code = {a.asset_code: a for a in assets}
    order = [pb["beat_id"] for pb in plan.get("beats") or []]
    wanted = set(beat_ids) if beat_ids else set(order)
    out = [dict(r, source="director") for r in plan.get("search_requests") or []
           if r.get("beat_id") in wanted]
    for pb in plan.get("beats") or []:
        if pb["beat_id"] not in wanted:
            continue
        blocked = blocked_at(blueprint, pb["beat_id"])
        out += entity_gaps(pb, entities, assets, blocked, profile, weak_tier)
        out += shot_gaps(pb, by_code, reqs.get(pb["beat_id"], {}))
    pos = {bid: k for k, bid in enumerate(order)}
    out.sort(key=lambda w: (SOURCE_ORDER.get(w.get("source"), 9),
                            pos.get(w.get("beat_id"), 0), w.get("from_sentence") or 0))
    return out


# ---------------------------------------------------------------------------
# requests (pure)
# ---------------------------------------------------------------------------


def case_place(case: Case, requirements: dict) -> str | None:
    """Where the case happens, as a search suffix ('Stendal'): the case's
    location, else the first map place of the plan, else the first place
    entity."""
    loc = getattr(case, "location", None)
    if loc:
        return loc.split(",")[0].strip() or None
    for b in requirements.get("beats") or []:
        if b.get("map_place"):
            return b["map_place"].split(",")[0].strip()
    for e in requirements.get("entities") or []:
        if e.get("type") == "place":
            return e["name"].split(",")[0].strip()
    return None


def case_region(case: Case, requirements: dict) -> str | None:
    """Town and region of the case as a search ('Tipp City Ohio'): the
    first two parts of the case's location or of the first map place,
    whichever is more precise ('Tipp City, Ohio, United States' over
    'Ohio, United States')."""
    options = [getattr(case, "location", None)] + [
        next((b["map_place"] for b in requirements.get("beats") or [] if b.get("map_place")),
             None)]
    options = [o for o in options if o]
    if not options:
        return None
    loc = max(options, key=lambda o: len([p for p in o.split(",") if p.strip()]))
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    return " ".join(parts[:2]) or None


def request_queries(w: dict, entity: dict | None, place: str | None, n: int,
                    country: str | None = None, region: str | None = None) -> list[dict]:
    """Up to n searches for one weak sentence: the director's queries,
    then the entity's name with the place of the case and — for
    buildings and places — its exterior and historical-photograph
    variants (a person: the name with the place and with the period),
    the plain name, then the planner's context queries and the case's
    region (the contextual fallback) — at least one of those whenever
    n >= 2."""
    if entity is None:
        name = w.get("entity_name") or ""
        entity = {"key": w.get("entity") or fold(name).replace(" ", "_"), "name": name,
                  "type": "object", "context_queries": [], "footage": False}
    name = (entity.get("name") or "").strip()
    etype = entity.get("type") or "object"
    suffix = place if place and fold(place) not in fold(name) else None
    if etype == "place" and not suffix and country and fold(country) not in fold(name):
        suffix = country
    base = f"{name} {suffix}" if suffix else name
    period = entity.get("period")
    if etype in ("building", "place"):
        exact = [base, f"{base} exterior", f"{base} historical photograph"]
    elif etype == "person":
        exact = [base] + ([f"{name} {period}"] if period else []) + [f"{name} photograph"]
    elif etype == "event":
        exact = [base] + ([f"{base} {period}"] if period else []) + [f"{base} news photograph"]
    else:
        exact = [base] + ([f"{name} {period}"] if period else []) + [f"{base} photograph"]
    exact.append(name)  # the plain name last: some archives index only that
    items = [query_item(entity, q, "exact") for q in w.get("queries") or []]
    items += [query_item(entity, q, "exact") for q in exact if q.strip()]
    context = list(entity.get("context_queries") or [])
    if region and fold(region) not in {fold(c) for c in context}:
        # the contextual fallback (tier 4): the town itself — streets,
        # landmarks, old postcards — when the real thing was never photographed
        context.append(region)
    items += [query_item(entity, q, "context") for q in context]
    out, seen = [], set()
    for q in items:
        k = fold(q["query"])
        if k and k not in seen:
            seen.add(k)
            q["footage"] = bool(entity.get("footage", etype in FOOTAGE_TYPES))
            out.append(q)
    if n >= 2 and not any(q["kind"] == "context" for q in out[:n]):
        ctx = [q for q in out if q["kind"] == "context"]
        if ctx:  # one stand-in search is always part of the request
            return out[:n - 1] + ctx[:1]
    return out[:n]


def build_requests(weak: list[dict], requirements: dict, case: Case) -> list[dict]:
    """One request per entity (its first weak sentence), bounded."""
    cfg = ai_config.visual_direction
    entities = {e["key"]: e for e in requirements.get("entities") or []}
    place = case_place(case, requirements)
    country = getattr(case, "country", None)
    region = case_region(case, requirements)
    out, seen = [], set()
    for w in weak:
        ident = w.get("entity") or fold(w.get("entity_name") or "")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        ent = entities.get(w.get("entity") or "")
        queries = request_queries(w, ent, place, cfg.queries_per_request, country, region)
        if not queries:
            continue
        out.append({**w, "entity_name": (ent or {}).get("name") or w.get("entity_name"),
                    "query_items": queries, "queries": [q["query"] for q in queries]})
        if len(out) >= cfg.max_search_requests:
            break
    return out


# ---------------------------------------------------------------------------
# the stage
# ---------------------------------------------------------------------------


def _found_for(request: dict, assets: list[VisualAsset]) -> list[VisualAsset]:
    queries = {fold(q) for q in request["queries"]}
    key = request.get("entity")
    out = []
    for a in assets:
        ents = _loads(a.entities_json, [])
        if (key and (a.entity_key == key or key in ents)) or fold(a.found_for or "") in queries:
            out.append(a)
    return out


async def fill_visual_gaps(db: Session, case: Case, plan_row: VisualPlan,
                           blueprint_row: EditorialBlueprint, audio_plan: AudioPlan | None,
                           profile: str | None = None, beat_ids: list[str] | None = None,
                           agent=None) -> dict:
    """The visual_gaps stage: find weak sentences, search, verify,
    re-direct the affected beats, re-materialize, store the audit."""
    cfg = ai_config.visual_direction
    if not cfg.production_search or cfg.max_search_requests <= 0:
        return {"skipped": True, "reason": "visual_direction.production_search is off"}
    from app.documentary.jobs import verify_assets
    from app.documentary.visuals.director import VisualDirector
    from app.documentary.visuals.generated import materialize
    from app.documentary.visuals.research import VisualResearchAgent

    plan = _loads(plan_row.plan_json, {})
    requirements = _loads(plan_row.requirements_json, {})
    blueprint = _loads(blueprint_row.blueprint_json, {})
    assets = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
    weak = weak_sentences(plan, requirements, assets, blueprint, beat_ids, profile)
    requests = build_requests(weak, requirements, case)
    result = {"weak_sentences": len(weak), "requests": len(requests), "queries": 0,
              "assets_found": 0, "verified": 0, "redirected_beats": [], "used": 0}
    audit: list[dict] = []
    if requests:
        before = {a.id for a in assets}
        queries = [q for r in requests for q in r["query_items"]]
        result["queries"] = len(queries)
        try:
            stats = await (agent or VisualResearchAgent()).run(
                db, case, queries, found_during="production_search")
            result["research"] = {k: v for k, v in (stats or {}).items()
                                  if k in ("candidates", "added", "duplicates", "rejected",
                                           "errors", "by_provider")}
            now = db.query(VisualAsset).filter(VisualAsset.case_id == case.id).all()
            new = [a for a in now if a.id not in before]
            # what this request's searches found — now or in an earlier
            # (interrupted) production search: unchecked ones are checked,
            # verified ones are offered to the director
            found_here = [a for a in now if a.id not in before
                          or a.found_during == "production_search"]
            per_request = {id(r): _found_for(r, found_here) for r in requests}
            todo: list[VisualAsset] = []
            for r in requests:
                for a in [x for x in per_request[id(r)]
                          if x.verification_status == "unverified"][:VERIFY_PER_REQUEST]:
                    if a not in todo:
                        todo.append(a)
            if todo:
                checked = await verify_assets(db, case, todo, requirements.get("entities") or [])
                result["verified"] = checked.get("verified", 0)
            result["assets_found"] = len(new)
            searched: dict[str, list[dict]] = {}
            for r in requests:
                found = sorted((a for a in per_request[id(r)] if usable(a, profile)
                                and a.verification_status == "verified"),
                               key=lambda a: (asset_tier(a), a.id))
                r["assets_found"] = [a.asset_code for a in per_request[id(r)]]
                if found:
                    searched.setdefault(r["beat_id"], []).append({
                        "from_sentence": r.get("from_sentence") or 0, "entity": r.get("entity"),
                        "entity_name": r.get("entity_name"), "why": r.get("why"),
                        "found": [a.asset_code for a in found]})
            if searched:
                out = await VisualDirector().redirect(
                    db, case, plan_row, blueprint_row, audio_plan, list(searched), profile,
                    searched)
                result["redirected_beats"] = out.get("redirected") or []
                await materialize(db, case, plan_row)
        except Exception as e:  # a failed search never stops the film
            log.warning("production search failed for case %s: %s", case.id, e)
            result["error"] = f"{type(e).__name__}: {e}"[:300]
    plan = _loads(plan_row.plan_json, {})
    shown: dict[str, list[dict]] = {}
    for pb in plan.get("beats") or []:
        for s in pb.get("shots") or []:
            if s.get("asset_id"):
                shown.setdefault(s["asset_id"], []).append(
                    {"beat_id": pb["beat_id"], "from_sentence": s.get("from_sentence")})
    for r in requests:
        found = r.get("assets_found") or []
        used_at = [dict(x, asset_id=c) for c in found for x in shown.get(c, [])]
        audit.append({"beat_id": r["beat_id"], "from_sentence": r.get("from_sentence"),
                      "sentence": r.get("sentence"), "entity": r.get("entity"),
                      "entity_name": r.get("entity_name"), "why": r.get("why"),
                      "source": r.get("source"), "queries": r["queries"],
                      "assets_found": found, "used": bool(used_at), "used_at": used_at})
    result["used"] = sum(1 for a in audit if a["used"])
    db.refresh(plan_row)
    validation = _loads(plan_row.validation_json, {})
    validation["search_requests"] = audit
    validation["production_search"] = {k: v for k, v in result.items() if k != "research"}
    plan_row.validation_json = json.dumps(validation, ensure_ascii=False)
    db.commit()
    return result
