"""The crime scene(s) of a case: named once from the evidence, the same for every language's film (every film shows a map of each of
them and of no other place). One model call per case; the answer is kept in `case_crime_scenes` and never asked again. Code checks
the answer against the evidence (a place the evidence never names is dropped) and falls back to the case's own location (the town)
when nothing survives; a case with no location either has no crime scene and the films say so."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, CaseCrimeScene, Fact, StoryVersion
from app.documentary.mvp.needs import fold
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)

KINDS = ("address", "city")
MIN_WORD = 3
MAX_PLACE_CHARS = 300


def head(place: str) -> str:
    """The most specific part of a place string: "Cunningham Court, Tipp City, Ohio" -> "Cunningham Court"."""
    return place.split(",", 1)[0].strip()


def words(text: str) -> set[str]:
    return {w for w in fold(text).split() if len(w) >= MIN_WORD}


def load(db: Session, case_id: int) -> list[CaseCrimeScene]:
    return (db.query(CaseCrimeScene).filter(CaseCrimeScene.case_id == case_id)
            .order_by(CaseCrimeScene.position, CaseCrimeScene.id).all())


def evidence_view(db: Session, case: Case) -> dict:
    """What the finder reads: the case's location, the places the facts mention and the strongest facts with their places."""
    cfg = ai_config.zoom().crime_maps
    ranked = db.query(Fact).filter(Fact.case_id == case.id).order_by(Fact.confidence.desc(), Fact.id).all()
    named = [f for f in ranked if json.loads(f.locations_json or "[]")][:cfg.location_facts]       # the street is often named only in a weak fact
    keep = {f.id for f in ranked[:cfg.evidence_facts]} | {f.id for f in named}
    facts = [f for f in ranked if f.id in keep]
    counts: dict[str, int] = {}
    items = []
    for f in facts:
        places = [str(p) for p in json.loads(f.locations_json or "[]") if p]
        for p in places:
            counts[p] = counts.get(p, 0) + 1
        items.append({"claim": (f.claim or "")[:300], "locations": places})
    top = [p for p, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:25]]
    return {"case": case.canonical_title, "case_location": case.location or "", "places_in_evidence": top, "facts": items}


def named_in_evidence(place: str, view: dict) -> bool:
    """True when every word of the place's most specific part stands in the case's location, a fact's places or a fact's claim."""
    wanted = words(head(place))
    if not wanted:
        return False
    known = words(view["case_location"])
    for item in view["facts"]:
        known |= words(item["claim"])
        for loc in item["locations"]:
            known |= words(loc)
    return wanted <= known


def clean(raw: dict, view: dict) -> list[dict]:
    """The agent's scenes that the evidence backs, once each, with a valid kind."""
    out, seen = [], set()
    for s in (raw or {}).get("scenes") or []:
        place = " ".join(str(s.get("place") or "").split())[:MAX_PLACE_CHARS]
        kind = s.get("kind") if s.get("kind") in KINDS else "city"
        key = fold(place)
        if not place or key in seen or not named_in_evidence(place, view):
            continue
        seen.add(key)
        out.append({"place": place, "kind": kind, "source": str(s.get("evidence") or "")[:MAX_PLACE_CHARS]})
    return out


def fallback(view: dict) -> list[dict]:
    """The case's own location (the town) when the evidence yields no crime scene of its own."""
    location = " ".join((view["case_location"] or "").split())[:MAX_PLACE_CHARS]
    return [{"place": location, "kind": "city", "source": "the case's location"}] if location else []


def mention_share(place: str, story_text: str) -> float | None:
    """How far into the story (0 to 1) the place is first named; None when the story never names it."""
    text, phrase = fold(story_text), fold(head(place))
    at = text.find(phrase) if phrase else -1
    return round(at / len(text), 4) if at >= 0 and text else None


def english_story(db: Session, case_id: int) -> str:
    draft = ai_config.language_pipeline.draft_language
    row = (db.query(StoryVersion).filter(StoryVersion.case_id == case_id, StoryVersion.language == draft)
           .order_by(StoryVersion.id.desc()).first())
    return row.story_text if row is not None else ""


async def ensure(db: Session, case: Case) -> list[CaseCrimeScene]:
    """The case's crime scenes: the stored ones, else found now (one model call), stored and returned."""
    stored = load(db, case.id)
    if stored:
        return stored
    view = evidence_view(db, case)
    with track_run(db, case.id, "Crime scenes", input_summary=f"facts={len(view['facts'])}") as run:
        raw, res = await run_agent("mvp.crime_scenes", get_generation_provider(), json.dumps(view, ensure_ascii=False))
        stamp_run(run, res, "mvp_crime_scene_finder")
    found = clean(raw, view) or fallback(view)
    story = english_story(db, case.id)
    for n, s in enumerate(found):
        db.add(CaseCrimeScene(case_id=case.id, place=s["place"], kind=s["kind"], position=n,
                              mention_share=mention_share(s["place"], story), source=s["source"]))
    db.commit()
    log.info("case %s: %d crime scene(s): %s", case.id, len(found), [s["place"] for s in found])
    return load(db, case.id)
