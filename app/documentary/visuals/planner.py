"""Visual requirement planner (Part 24): what each beat NEEDS to be
seen — by semantic documentary need, never sentence by sentence.

The planner (role visual_planner) reads the blueprint beats and the
evidence and returns:
  * entities  — people, places, buildings, vehicles, objects, documents
                worth searching for, with search queries;
  * per beat  — requirements (entity, purpose, priority, period,
                acceptable roles), and optional generated material:
                a map place, a date, a short exact quote, a document
                passage (all checked against the evidence).
A deterministic validator drops anything not grounded in the evidence.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from sqlalchemy.orm import Session

from app.agents.story import build_evidence_pack
from app.core.ai_config import ai_config
from app.db.models import (
    Case, Contradiction, EditorialBlueprint, Fact, Source, VisualPlan,
)
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

ENTITY_TYPES = {"person", "place", "building", "vehicle", "object", "document",
                "organization", "event", "landscape"}
PURPOSES = {"human_connection", "orientation", "evidence", "atmosphere", "time",
            "investigation", "emotion", "transition"}
PRIORITIES = {"high", "medium", "low"}
ROLES = {"evidence", "context", "illustration"}

PLANNER_SYSTEM = """
You are the visual researcher of a high-end true-crime documentary
(real photos, documents, maps, archive — no AI imagery). For a story
that is already written and divided into beats, decide what the viewer
should SEE in each beat and what to search for. Think like a documentary
editor: by need (a person's face when we meet them, the place when we
arrive there, the document when it matters), not by sentence.

Return:
1. entities — every person, place, building, vehicle, object or
   document worth finding real images of. For each: key (snake_case),
   type (person|place|building|vehicle|object|document|organization|
   event|landscape), name, period (year or range, if relevant), and 2–4
   search_queries in English that would find real photos (name + place,
   name + year; places with their region and country).
2. beats — for every beat:
   - requirements: [{"entity": key, "purpose": human_connection|
     orientation|evidence|atmosphere|time|investigation|emotion|
     transition, "priority": high|medium|low, "acceptable_roles":
     ["evidence","context","illustration"]}] — at most 3, most
     important first. Use "illustration" only for atmosphere, never
     for a claim about the case.
   - map_place: a real place name with region and country when the
     beat moves the viewer somewhere new (else null).
   - date_text: the date the beat is anchored to, in English, exactly
     as in the evidence (else null).
   - quote: {"fact_id": "F012", "text": "<= 14 words, copied EXACTLY
     from that fact's claim or supporting_text"} when a short real
     quotation deserves to be read on screen (rare; else null).
   - document: {"source_id": 17, "passage": "a sentence copied EXACTLY
     from that source's text"} when the beat is about a real document
     (coroner finding, police statement, letter; rare; else null).
Do not invent places, dates or quotes. Everything must come from the
evidence given.

Return JSON only:
{"entities": [{"key": "...", "type": "person", "name": "...", "period": "2007",
  "search_queries": ["..."]}],
 "beats": [{"beat_id": "B01", "requirements": [...], "map_place": null,
   "date_text": null, "quote": null, "document": null}]}
"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("“", '"')
                  .replace("”", '"')).strip().lower()


def _planner_input(case: Case, blueprint: dict, pack: dict, sources: list[Source]) -> dict:
    people, places = Counter(), Counter()
    for f in pack["facts"]:
        for p in f.get("people") or []:
            people[str(p)] += 1
        for loc in f.get("locations") or []:
            places[str(loc)] += 1
    return {
        "case": case.canonical_title,
        "central_question": blueprint.get("central_question"),
        "beats": [
            {"beat_id": b["id"], "purpose": b.get("purpose"),
             "summary": b.get("summary"), "human_focus": b.get("human_focus"),
             "visual_intent": b.get("visual_intent"), "reveals": b.get("reveals"),
             "attention": b.get("attention")}
            for b in blueprint.get("beats") or []
        ],
        "people_in_evidence": [p for p, _ in people.most_common(25)],
        "places_in_evidence": [p for p, _ in places.most_common(25)],
        "facts": [
            {"id": f["id"], "claim": f["claim"], "date": None,
             "supporting_text": (f.get("supporting_text") or "")[:300]}
            for f in pack["facts"]
        ],
        "timeline": pack.get("timeline", [])[:80],
        "sources": [
            {"source_id": s.id, "title": s.title, "publisher": s.publisher,
             "type": s.source_type, "has_text": bool(s.raw_text)}
            for s in sources
        ],
    }


def validate_requirements(raw: dict, blueprint: dict, pack: dict,
                          sources: list[Source]) -> tuple[dict, dict]:
    raw = raw if isinstance(raw, dict) else {}
    warnings: list[dict] = []
    entities: dict[str, dict] = {}
    for e in raw.get("entities") or []:
        if not isinstance(e, dict) or not e.get("key") or not e.get("name"):
            continue
        key = re.sub(r"[^a-z0-9_]+", "_", str(e["key"]).lower()).strip("_")[:60]
        queries = [str(q)[:120] for q in e.get("search_queries") or [] if str(q).strip()][:4]
        entities[key] = {
            "key": key, "type": e.get("type") if e.get("type") in ENTITY_TYPES else "object",
            "name": str(e["name"])[:200], "period": (str(e.get("period"))[:40] if e.get("period") else None),
            "search_queries": queries or [str(e["name"])[:120]],
        }
    facts = {f["id"]: f for f in pack["facts"]}
    src_text = {s.id: _norm(s.raw_text or "") for s in sources}
    known = {b["id"] for b in blueprint.get("beats") or []}
    given = {str(b.get("beat_id")): b for b in raw.get("beats") or [] if isinstance(b, dict)}
    beats = []
    for bid in [b["id"] for b in blueprint.get("beats") or []]:
        g = given.get(bid) or {}
        reqs = []
        for r in (g.get("requirements") or [])[:3]:
            if not isinstance(r, dict):
                continue
            key = re.sub(r"[^a-z0-9_]+", "_", str(r.get("entity") or "").lower()).strip("_")
            if key not in entities:
                warnings.append({"code": "unknown_entity", "beat": bid, "entity": key})
                continue
            roles = [x for x in r.get("acceptable_roles") or [] if x in ROLES] or ["evidence", "context"]
            reqs.append({
                "entity": key,
                "purpose": r.get("purpose") if r.get("purpose") in PURPOSES else "atmosphere",
                "priority": r.get("priority") if r.get("priority") in PRIORITIES else "medium",
                "acceptable_roles": roles,
            })
        quote = g.get("quote") if isinstance(g.get("quote"), dict) else None
        if quote:
            f = facts.get(str(quote.get("fact_id")))
            text = str(quote.get("text") or "")
            hay = _norm((f or {}).get("claim", "")) + " " + _norm((f or {}).get("supporting_text") or "")
            words = len(text.split())
            fragment = words < 4 or (text[:1].islower() and not text[:1].isdigit())
            if (not f or not text or _norm(text) not in hay or fragment
                    or words > ai_config.attention.max_overlay_words):
                warnings.append({"code": "quote_dropped", "beat": bid})
                quote = None
            else:
                quote = {"fact_id": f["id"], "text": text}
        document = g.get("document") if isinstance(g.get("document"), dict) else None
        if document:
            try:
                sid = int(document.get("source_id"))
            except (TypeError, ValueError):
                sid = None
            passage = str(document.get("passage") or "")
            if sid not in src_text or not passage or _norm(passage) not in src_text[sid]:
                warnings.append({"code": "document_dropped", "beat": bid})
                document = None
            else:
                document = {"source_id": sid, "passage": passage}
        date_text = g.get("date_text")
        if date_text and not any(
                _norm(str(date_text)) in _norm(f.get("claim", "")) for f in pack["facts"]):
            # Dates are formatted freely by models; keep only if the year
            # appears in the evidence timeline.
            years = re.findall(r"\b(1[89]\d\d|20\d\d)\b", str(date_text))
            ev_years = {y for t in pack.get("timeline", [])
                        for y in re.findall(r"\b(1[89]\d\d|20\d\d)\b", str(t.get("event_date")))}
            if not years or not set(years) <= ev_years:
                warnings.append({"code": "date_dropped", "beat": bid})
                date_text = None
        beats.append({
            "beat_id": bid, "requirements": reqs,
            "map_place": str(g["map_place"])[:150] if g.get("map_place") else None,
            "date_text": str(date_text)[:60] if date_text else None,
            "quote": quote, "document": document,
        })
    for bid in given:
        if bid not in known:
            warnings.append({"code": "unknown_beat", "beat": bid})
    errors = [] if entities and beats else [{"code": "empty_requirements"}]
    report = {"status": "invalid" if errors else ("needs_review" if warnings else "valid"),
              "errors": errors, "warnings": warnings,
              "entities": len(entities),
              "beats_with_needs": sum(1 for b in beats if b["requirements"])}
    return {"entities": list(entities.values()), "beats": beats}, report


def research_queries(requirements: dict, beat_ids: list[str] | None = None) -> list[dict]:
    """Search queries ordered by need: entities of high-priority
    requirements in the given beats first."""
    weight: Counter = Counter()
    for b in requirements.get("beats") or []:
        if beat_ids and b["beat_id"] not in beat_ids:
            continue
        for r in b["requirements"]:
            weight[r["entity"]] += {"high": 3, "medium": 2, "low": 1}[r["priority"]]
    ents = {e["key"]: e for e in requirements.get("entities") or []}
    out = []
    for key, _ in weight.most_common():
        e = ents.get(key)
        if not e:
            continue
        for q in e["search_queries"]:
            out.append({"query": q, "entity": key, "entities": [key]})
    return out


def _evidence(db: Session, case_id: int):
    facts = db.query(Fact).filter(Fact.case_id == case_id).all()
    contras = db.query(Contradiction).filter(Contradiction.case_id == case_id).all()
    sources = db.query(Source).filter(Source.case_id == case_id).all()
    return build_evidence_pack(facts, contras, sources), sources


class VisualPlanner:
    def __init__(self):
        self.gen = get_generation_provider()

    async def create(self, db: Session, case: Case,
                     blueprint_row: EditorialBlueprint) -> VisualPlan:
        blueprint = json.loads(blueprint_row.blueprint_json or "{}")
        pack, sources = _evidence(db, case.id)
        payload = _planner_input(case, blueprint, pack, sources)
        with track_run(db, case.id, "Visual Planner",
                       input_summary=f"blueprint={blueprint_row.id}") as run:
            raw, res = await self.gen.generate_structured(
                "visual_planner", PLANNER_SYSTEM, json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, "visual_planner")
        reqs, report = validate_requirements(raw, blueprint, pack, sources)
        count = db.query(VisualPlan).filter(VisualPlan.blueprint_id == blueprint_row.id).count()
        row = VisualPlan(
            case_id=case.id, blueprint_id=blueprint_row.id, version=count + 1,
            status="requirements" if report["status"] != "invalid" else "invalid",
            requirements_json=json.dumps(reqs, ensure_ascii=False),
            validation_json=json.dumps({"requirements": report}, ensure_ascii=False),
            generation_model=getattr(res, "model", None),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
