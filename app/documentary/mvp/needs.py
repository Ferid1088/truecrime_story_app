"""Step 4 - graph-aware visual needs. The planner agent turns the blueprint's beats, the evidence and
the case's reveal graph into per-beat requirements; deterministic checks drop everything the evidence
or the graph does not support, and the surviving requirements are stored as MvpRequirement rows."""

from __future__ import annotations

import json
import re
import unicodedata

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.member_ids import ensure_pipeline_member_ids
from app.db.models import (
    Case,
    EditorialBlueprint,
    MvpRequirement,
    RevealGraph,
    Source,
    VisualPlan,
)
from app.providers.generation import get_generation_provider
from app.agents.story import build_evidence_pack
from app.services.tracking import stamp_run, track_run

FUNCTIONS = ("human_connection", "orientation", "evidence", "atmosphere", "time", "investigation",
             "emotion", "transition")
ROLES = ("evidence", "context", "illustration")
PRIORITIES = ("high", "medium", "low")
ALTERNATIVES = ("photo", "footage", "document", "map", "timeline", "quote_card", "board")
ENTITY_TYPES = ("person", "place", "building", "object", "vehicle", "event", "document", "organization")
FOOTAGE_TYPES = {"place", "event", "building", "vehicle"}
MAX_REQUIREMENTS_PER_BEAT = 4
YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def fold(text: str) -> str:
    """Case- and accent-insensitive form used to compare evidence wording."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def _strings(values, limit: int, max_chars: int = 120) -> list[str]:
    out: list[str] = []
    for v in values or []:
        s = str(v).strip()[:max_chars]
        if s and s not in out:
            out.append(s)
    return out[:limit]


def _key(value) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").lower()).strip("_")


def graph_view(graph: RevealGraph, beat_order: dict[str, int]) -> dict:
    """The graph as the planner sees it: nodes with their first beat, and the prerequisite edges."""
    keys = {n.id: n.node_key for n in graph.nodes}
    return {
        "nodes": [{"key": n.node_key, "label": n.label, "category": n.category,
                   "first_beat": n.first_revealed_beat_id, "first_order": n.first_revealed_order}
                  for n in sorted(graph.nodes, key=lambda n: (n.first_revealed_order, n.node_key))],
        "edges": [[keys[e.prerequisite_node_id], keys[e.dependent_node_id]] for e in graph.edges],
        "beat_order": beat_order,
    }


def planner_input(case: Case, blueprint: dict, pack: dict, sources: list[Source],
                  graph: dict, seconds: dict[str, float]) -> dict:
    order = {b["id"]: i for i, b in enumerate(blueprint.get("beats") or [])}
    people: dict[str, int] = {}
    places: dict[str, int] = {}
    for f in pack["facts"]:
        for p in f.get("people") or []:
            people[str(p)] = people.get(str(p), 0) + 1
        for loc in f.get("locations") or []:
            places[str(loc)] = places.get(str(loc), 0) + 1
    top = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:25]]  # noqa: E731
    return {
        "case": case.canonical_title,
        "central_question": blueprint.get("central_question"),
        "beats": [{"beat_id": b["id"], "order": order[b["id"]], "purpose": b.get("purpose"),
                   "summary": b.get("summary"), "human_focus": b.get("human_focus"),
                   "visual_intent": b.get("visual_intent"), "attention": b.get("attention"),
                   "reveals": b.get("reveals"), "seconds": round(seconds.get(b["id"], 0.0), 1)}
                  for b in blueprint.get("beats") or []],
        "graph": {k: graph[k] for k in ("nodes", "edges")},
        "people_in_evidence": top(people),
        "places_in_evidence": top(places),
        "facts": [{"id": f["id"], "claim": f["claim"],
                   "supporting_text": (f.get("supporting_text") or "")[:300]} for f in pack["facts"]],
        "timeline": pack.get("timeline", [])[:80],
        "sources": [{"source_id": s.id, "title": s.title, "publisher": s.publisher,
                     "type": s.source_type, "has_text": bool(s.raw_text)} for s in sources],
    }


def _clean_entity(e: dict) -> tuple[str, dict] | None:
    key, name = _key(e.get("key")), str(e.get("name") or "").strip()[:160]
    if not key or not name:
        return None
    etype = e.get("type") if e.get("type") in ENTITY_TYPES else "object"
    limits = ai_config.limits_settings().planner
    footage = e.get("footage")
    return key, {
        "key": key, "type": etype, "name": name,
        "period": str(e["period"])[:40] if e.get("period") else None,
        "aliases": _strings(e.get("aliases"), limits.max_aliases, 80),
        "search_queries": _strings(e.get("search_queries"), limits.max_exact_queries) or [name[:120]],
        "context_queries": _strings(e.get("context_queries"), limits.max_context_queries),
        "footage": footage if isinstance(footage, bool) else etype in FOOTAGE_TYPES,
    }


def validate_needs(raw: dict, blueprint: dict, pack: dict, sources: list[Source],
                   graph: dict) -> tuple[dict, dict]:
    """Keep only what the evidence and the reveal graph support. Returns (requirements, report)."""
    raw = raw if isinstance(raw, dict) else {}
    warnings: list[dict] = []
    entities: dict[str, dict] = {}
    for e in raw.get("entities") or []:
        cleaned = _clean_entity(e) if isinstance(e, dict) else None
        if cleaned:
            entities[cleaned[0]] = cleaned[1]
    nodes = {n["key"]: n for n in graph["nodes"]}
    beat_order = {b["id"]: i for i, b in enumerate(blueprint.get("beats") or [])}
    facts = {f["id"]: f for f in pack["facts"]}
    src_text = {s.id: fold(s.raw_text or "") for s in sources}
    ev_years = {y for t in pack.get("timeline", []) for y in YEAR.findall(str(t.get("event_date")))}
    given = {str(b.get("beat_id")): b for b in raw.get("beats") or [] if isinstance(b, dict)}
    beats = []
    for bid in beat_order:
        g = given.get(bid) or {}
        reqs = []
        for r in (g.get("requirements") or [])[:MAX_REQUIREMENTS_PER_BEAT]:
            if not isinstance(r, dict):
                continue
            entity = _key(r.get("entity"))
            if entity not in entities:
                warnings.append({"code": "unknown_entity", "beat": bid, "entity": entity})
                continue
            node_key = r.get("node_key") or None
            if node_key is not None:
                node = nodes.get(str(node_key))
                if node is None:
                    warnings.append({"code": "unknown_graph_node", "beat": bid, "node": node_key})
                    node_key = None
                elif node["first_order"] > beat_order[bid]:
                    warnings.append({"code": "later_reveal", "beat": bid, "node": node_key})
                    continue
            role = r.get("role_needed") if r.get("role_needed") in ROLES else "context"
            accepted = [x for x in r.get("acceptable_roles") or [] if x in ROLES] or [role]
            reqs.append({
                "entity": entity, "node_key": node_key,
                "function": r.get("function") if r.get("function") in FUNCTIONS else "atmosphere",
                "role_needed": role, "acceptable_roles": sorted(set(accepted) | {role}),
                "priority": r.get("priority") if r.get("priority") in PRIORITIES else "medium",
                "alternatives": [a for a in r.get("alternatives") or [] if a in ALTERNATIVES],
                "need": str(r.get("need") or "")[:200],
            })
        quote = g.get("quote") if isinstance(g.get("quote"), dict) else None
        if quote:
            fact = facts.get(str(quote.get("fact_id")))
            text = str(quote.get("text") or "")
            hay = fold((fact or {}).get("claim", "")) + " " + fold((fact or {}).get("supporting_text") or "")
            words = len(text.split())
            if (not fact or not text or fold(text) not in hay or words < 4
                    or (text[:1].islower() and not text[:1].isdigit())
                    or words > ai_config.attention.max_overlay_words):
                warnings.append({"code": "quote_dropped", "beat": bid})
                quote = None
            else:
                quote = {"fact_id": fact["id"], "text": text}
        document = g.get("document") if isinstance(g.get("document"), dict) else None
        if document:
            try:
                sid = int(document.get("source_id"))
            except (TypeError, ValueError):
                sid = None
            passage = str(document.get("passage") or "")
            if sid not in src_text or not passage or fold(passage) not in src_text[sid]:
                warnings.append({"code": "document_dropped", "beat": bid})
                document = None
            else:
                document = {"source_id": sid, "passage": passage}
        date_text = g.get("date_text")
        if date_text:
            years = YEAR.findall(str(date_text))
            if not years or not set(years) <= ev_years:
                warnings.append({"code": "date_dropped", "beat": bid})
                date_text = None
        beats.append({"beat_id": bid, "requirements": reqs,
                      "map_place": str(g["map_place"])[:150] if g.get("map_place") else None,
                      "date_text": str(date_text)[:60] if date_text else None,
                      "quote": quote, "document": document})
    for bid in given:
        if bid not in beat_order:
            warnings.append({"code": "unknown_beat", "beat": bid})
    uncovered = [b["beat_id"] for b in beats if not b["requirements"]]
    errors = [] if entities and any(b["requirements"] for b in beats) else [{"code": "empty_requirements"}]
    report = {"status": "invalid" if errors else ("needs_review" if warnings else "valid"),
              "errors": errors, "warnings": warnings, "entities": len(entities),
              "beats_with_needs": len(beats) - len(uncovered), "beats_without_needs": uncovered}
    return {"entities": list(entities.values()), "beats": beats}, report


def store_requirements(db: Session, plan: VisualPlan, graph_id: int | None, requirements: dict) -> int:
    """One MvpRequirement row per surviving requirement (replacing the plan's previous rows)."""
    db.query(MvpRequirement).filter(MvpRequirement.visual_plan_id == plan.id).delete()
    count = 0
    for beat in requirements["beats"]:
        for index, r in enumerate(beat["requirements"]):
            db.add(MvpRequirement(
                case_id=plan.case_id, visual_plan_id=plan.id, graph_id=graph_id,
                requirement_key=f"{beat['beat_id']}.{index + 1}", beat_id=beat["beat_id"],
                node_key=r["node_key"], function=r["function"], entity_key=r["entity"],
                role_needed=r["role_needed"], priority=r["priority"],
                spec_json=json.dumps({k: r[k] for k in ("acceptable_roles", "alternatives", "need")},
                                     ensure_ascii=False)))
            count += 1
    db.commit()
    return count


def _is_needs_plan(row: VisualPlan) -> bool:
    """The shared needs plan of a blueprint, not one of its per-language shot-list rows."""
    mvp = json.loads(row.validation_json or "{}").get("mvp")
    return mvp is not None and mvp.get("language") is None


def latest_mvp_plan(db: Session, blueprint_id: int) -> VisualPlan | None:
    rows = (db.query(VisualPlan).filter(VisualPlan.blueprint_id == blueprint_id)
            .order_by(VisualPlan.version.desc()).all())
    return next((r for r in rows if r.status != "invalid" and _is_needs_plan(r)), None)


async def _agent_needs(db: Session, case: Case, blueprint: dict, pack: dict, sources: list[Source], view: dict,
                       seconds: dict[str, float]) -> tuple[dict, dict, str | None]:
    """What the free stretches of the film need, from one planner call. `seconds` is each beat's free time: what the
    toolkit's fixed cards leave over. The toolkit has already made the reveal graph and its cards; the planner only
    reads them and never calls `app.documentary.detective.director`."""
    payload = planner_input(case, blueprint, pack, sources, view, seconds)
    with track_run(db, case.id, "Main Visual Planner: needs", input_summary=f"graph beats={len(seconds)}") as run:
        raw, res = await run_agent("mvp.needs", get_generation_provider(), json.dumps(payload, ensure_ascii=False))
        stamp_run(run, res, "mvp_needs_planner")
    requirements, report = validate_needs(raw, blueprint, pack, sources, view)
    return requirements, report, getattr(res, "model", None)


async def plan_needs(db: Session, case: Case, blueprint_row: EditorialBlueprint,
                     graph: RevealGraph, seconds: dict[str, float], replan: bool = False) -> VisualPlan:
    """The needs plan for one blueprint: reused when a valid one exists for the same graph, unless the
    owner asks to plan again - then a new version is made beside it and the earlier plan, with its
    candidates and verdicts, stays exactly as it was."""
    existing = latest_mvp_plan(db, blueprint_row.id)
    if not replan and existing is not None and json.loads(existing.validation_json)["mvp"].get(
            "reveal_graph_id") == graph.id:
        return existing
    blueprint = json.loads(blueprint_row.blueprint_json or "{}")
    facts_sources = build_pack(db, case.id)
    pack, sources = facts_sources
    beat_order = {b["id"]: i for i, b in enumerate(blueprint.get("beats") or [])}
    view = graph_view(graph, beat_order)
    requirements, report, model = await _agent_needs(db, case, blueprint, pack, sources, view, seconds)
    requirements = ensure_pipeline_member_ids(f"mvp-needs:{blueprint_row.id}", requirements)
    version = db.query(VisualPlan).filter(VisualPlan.blueprint_id == blueprint_row.id).count() + 1
    plan = VisualPlan(
        case_id=case.id, blueprint_id=blueprint_row.id, version=version,
        status="requirements" if report["status"] != "invalid" else "invalid",
        requirements_json=json.dumps(requirements, ensure_ascii=False),
        validation_json=json.dumps({"requirements": report, "mvp": {"reveal_graph_id": graph.id,
                                                                    "model": model}},
                                   ensure_ascii=False),
        generation_model=model)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    if plan.status != "invalid":
        store_requirements(db, plan, graph.id, requirements)
    return plan


def build_pack(db: Session, case_id: int):
    from app.db.models import Contradiction, Fact

    facts = db.query(Fact).filter(Fact.case_id == case_id).all()
    contras = db.query(Contradiction).filter(Contradiction.case_id == case_id).all()
    sources = db.query(Source).filter(Source.case_id == case_id).all()
    return build_evidence_pack(facts, contras, sources), sources
