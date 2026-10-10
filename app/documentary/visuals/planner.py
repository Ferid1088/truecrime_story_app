"""Visual requirement planner (Part 24): what each beat NEEDS to be
seen — by semantic documentary need, never sentence by sentence.

The planner (role visual_planner) reads the blueprint beats and the
evidence and returns:
  * entities  — people, places, buildings, vehicles, objects, events,
                documents, organizations worth searching for, each with
                EXACT search queries (the thing itself), CONTEXT queries
                (an accurate stand-in when the exact thing may never have
                been photographed: the region's forest, the town's old
                centre, the same car model shown clearly as context), the
                aliases the narration uses, and whether moving pictures
                (footage) would help;
  * per beat  — requirements (entity, purpose, priority, period,
                acceptable roles), and optional generated material:
                a map place, a date, a short exact quote, a document
                passage (all checked against the evidence).
A deterministic validator drops anything not grounded in the evidence.

sentence_entities() is the deterministic link between the narration and
these entities: which entities a sentence names (full names, a person's
last name, place names, aliases — case- and accent-insensitive). The
Visual Director, the gap check and the repetition control all ask it
"what is this sentence about?".
"""

from __future__ import annotations

from app.core.prompts import prompt

import json
import re
import unicodedata
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
                "organization", "event"}
# Older plans (and models) call open country "landscape": it is a place.
TYPE_ALIASES = {"landscape": "place", "location": "place", "city": "place",
                "town": "place", "car": "vehicle", "company": "organization"}
# Moving pictures help where something moves or happens (a town, a search,
# a trial): the default when the planner says nothing.
FOOTAGE_TYPES = {"place", "event"}
MAX_EXACT_QUERIES = 4
MAX_CONTEXT_QUERIES = 3
MAX_ALIASES = 5
PURPOSES = {"human_connection", "orientation", "evidence", "atmosphere", "time",
            "investigation", "emotion", "transition"}
PRIORITIES = {"high", "medium", "low"}
ROLES = {"evidence", "context", "illustration"}

PLANNER_SYSTEM = prompt("documentary/visuals/planner/planner_system")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("“", '"')
                  .replace("”", '"')).strip().lower()


def fold(text: str) -> str:
    """Lower case, accents removed, ß -> ss: 'Gehricke' == 'GEHRICKE',
    'Zürich' == 'Zurich'. Only for matching, never for display."""
    t = (text or "").replace("ß", "ss").replace("’", "'")
    t = unicodedata.normalize("NFKD", t)
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", fold(text))


def _strings(values, limit: int, max_chars: int = 120) -> list[str]:
    out: list[str] = []
    for v in values if isinstance(values, list) else []:
        s = " ".join(str(v).split())[:max_chars]
        if s and s.lower() not in {x.lower() for x in out}:
            out.append(s)
    return out[:limit]


def entity_type(value) -> str:
    t = str(value or "").strip().lower()
    t = TYPE_ALIASES.get(t, t)
    return t if t in ENTITY_TYPES else "object"


def _clean_entity(e: dict) -> tuple[str, dict]:
    key = re.sub(r"[^a-z0-9_]+", "_", str(e["key"]).lower()).strip("_")[:60]
    etype = entity_type(e.get("type"))
    name = str(e["name"])[:200]
    footage = e.get("footage")
    return key, {
        "key": key, "type": etype, "name": name,
        "period": (str(e.get("period"))[:40] if e.get("period") else None),
        "aliases": _strings(e.get("aliases"), MAX_ALIASES, 80),
        "search_queries": _strings(e.get("search_queries"), MAX_EXACT_QUERIES) or [name[:120]],
        "context_queries": _strings(e.get("context_queries"), MAX_CONTEXT_QUERIES),
        "footage": footage if isinstance(footage, bool) else etype in FOOTAGE_TYPES,
    }


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
        key, entity = _clean_entity(e)
        if key:
            entities[key] = entity
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


def query_item(entity: dict, query: str, kind: str = "exact") -> dict:
    """One search for VisualResearchAgent.run: what to search, for which
    entity (key and type: the asset's library fields and provisional
    tier), exact or context, and whether footage is searched too."""
    etype = entity.get("type") or "object"
    return {"query": query, "entity": entity["key"], "entities": [entity["key"]],
            "entity_type": etype, "kind": "context" if kind == "context" else "exact",
            "footage": bool(entity.get("footage", etype in FOOTAGE_TYPES))}


def research_queries(requirements: dict, beat_ids: list[str] | None = None,
                     max_queries: int | None = None) -> list[dict]:
    """Search queries ordered by need: entities of high-priority
    requirements in the given beats first; every EXACT query before any
    CONTEXT query (a stand-in is only wanted where the real thing is
    missing, and the research budget runs out from the end), at most
    visual_search.max_queries."""
    weight: Counter = Counter()
    for b in requirements.get("beats") or []:
        if beat_ids and b["beat_id"] not in beat_ids:
            continue
        for r in b["requirements"]:
            weight[r["entity"]] += {"high": 3, "medium": 2, "low": 1}.get(r.get("priority"), 2)
    ents = {e["key"]: e for e in requirements.get("entities") or []}
    ordered = [ents[k] for k, _ in weight.most_common() if k in ents]
    exact = [query_item(e, q, "exact") for e in ordered for q in e.get("search_queries") or []]
    context = [query_item(e, q, "context") for e in ordered
               for q in e.get("context_queries") or []]
    out, seen = [], set()
    for q in exact + context:
        k = (fold(q["query"]), q["kind"])
        if k not in seen:
            seen.add(k)
            out.append(q)
    limit = ai_config.visual_search.max_queries if max_queries is None else max_queries
    return out[:limit]


# ---------------------------------------------------------------------------
# which entities a sentence names
# ---------------------------------------------------------------------------

# Leading words that do not identify anything ("the church" names the
# church only through the rest).
_LEADING = {"the", "a", "an", "der", "die", "das"}
# Parts of a person's name that are never a last name on their own.
_NAME_PARTICLES = {"van", "von", "de", "der", "den", "la", "le", "du", "jr", "sr", "ii", "iii"}


def entity_patterns(entity: dict) -> list[tuple[str, ...]]:
    """Token sequences that name an entity in a sentence: its full name,
    aliases, the place part before a comma ('Nannup, Western Australia'
    -> 'Nannup'), and a person's last name (at least 3 letters)."""
    names = [entity.get("name") or ""] + list(entity.get("aliases") or [])
    pats: list[tuple[str, ...]] = []

    def add(tokens: list[str]):
        while len(tokens) > 1 and tokens[0] in _LEADING:
            tokens = tokens[1:]
        t = tuple(tokens)
        if t and t not in pats and not (len(t) == 1 and (len(t[0]) < 3 or t[0] in _LEADING)):
            pats.append(t)

    for n in names:
        add(_tokens(n))
        if "," in n:
            add(_tokens(n.split(",")[0]))
    if entity.get("type") == "person":
        full = [t for t in _tokens(entity.get("name") or "") if t not in _NAME_PARTICLES]
        if len(full) >= 2 and len(full[-1]) >= 3:
            add([full[-1]])
    return pats


def sentence_entities(text: str, entities: list[dict]) -> list[str]:
    """Keys of the planner entities a sentence names, in the order the
    sentence names them. Deterministic, case- and accent-insensitive,
    whole words only ('Ann' does not match 'Anna')."""
    tokens = _tokens(text)
    if not tokens:
        return []
    hay = " " + " ".join(tokens) + " "
    found: list[tuple[int, str]] = []
    for e in entities or []:
        best = None
        for pat in entity_patterns(e):
            i = hay.find(" " + " ".join(pat) + " ")
            if i >= 0 and (best is None or i < best):
                best = i
        if best is not None:
            found.append((best, e["key"]))
    return [k for _, k in sorted(found)]


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
