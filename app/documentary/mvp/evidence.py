"""From the case's stored evidence to the planner's alternatives: gathers what the graphics are built from, asks
the two small agents for the structured conflicts, short lines and headline placements (kept on the needs plan),
builds every offer, and puts the text of a placed graphic into the film's language."""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.db.models import Case, Contradiction, EditorialBlueprint, VisualPlan
from app.documentary.chapters import latest_chapter_plan
from app.documentary.graphics import shots as GS
from app.documentary.graphics import theme as T
from app.documentary.graphics.engines import kit
from app.documentary.mvp import alternatives as AL
from app.documentary.mvp import copy as CP
from app.documentary.mvp import engines as EN
from app.documentary.mvp import headlines as HL
from app.documentary.mvp.direct import beat_windows
from app.documentary.mvp.needs import build_pack

QUOTE_IN_CLAIM = re.compile(r'[“"]([^"”]{12,220})[”"]')


def gather(db: Session, case: Case, bp_row: EditorialBlueprint, needs_plan: VisualPlan, manifest: dict) -> AL.Evidence:
    blueprint = json.loads(bp_row.blueprint_json or "{}")
    beats = blueprint.get("beats") or []
    pack, source_rows = build_pack(db, case.id)
    facts = {f["id"]: f for f in pack["facts"]}
    contradictions = []
    for n, row in enumerate(sorted(db.query(Contradiction).filter_by(case_id=case.id).all(), key=lambda c: c.id), 1):
        contradictions.append({"id": f"C{n:03d}", "topic": row.topic, "description": row.description,
                               "source_ids": json.loads(row.source_ids_json or "[]")})
    sources = {s.id: {"publisher": s.publisher or "", "title": s.title or "", "url": s.url or "",
                      "published_at": s.published_at, "source_type": s.source_type, "language": s.language}
               for s in source_rows}
    chapter = latest_chapter_plan(db, bp_row.id)
    events = (json.loads(chapter.plan_json or "{}").get("events") or []) if chapter else []
    windows = {w["beat_id"]: w for w in beat_windows(manifest)}
    requirements = json.loads(needs_plan.requirements_json or "{}")
    quotes: list[dict] = []
    for b in requirements.get("beats") or []:
        q = b.get("quote")
        if q and q.get("fact_id") and q.get("text"):
            quotes.append({"text": q["text"], "fact_id": q["fact_id"], "beat": b["beat_id"],
                           "speaker": (facts.get(q["fact_id"]) or {}).get("speaker")})
        doc = b.get("document")
        src = sources.get(int(doc["source_id"])) if doc and str(doc.get("source_id", "")).isdigit() else None
        if doc and src and 12 <= len(doc["passage"]) <= 220:
            quotes.append({"text": doc["passage"], "fact_id": f"S{doc['source_id']}", "beat": b["beat_id"],
                           "speaker": src["publisher"]})
    for fact in pack.get("approved_quotes") or []:
        m = QUOTE_IN_CLAIM.search(fact["claim"])
        if m:
            quotes.append({"text": m.group(1).strip(), "fact_id": fact["id"], "beat": None,
                           "speaker": fact.get("speaker")})
    official = {s.id: {"text": s.raw_text, "type": HL.clean_title(s.title or "", s.publisher or "", s.publisher or ""),
                       "issuer": s.publisher or "", "published_at": s.published_at, "language": s.language or "en"}
                for s in source_rows if s.source_type == "official" and s.raw_text and s.publisher and s.title}
    return AL.Evidence(
        official=official, order={b["id"]: i for i, b in enumerate(beats)}, beats=beats, questions=blueprint.get("questions") or [],
        facts=facts, contradictions=contradictions, sources=sources, events=events, quotes=quotes,
        narration={bid: " ".join(s["speech"] for s in w["sentences"]) for bid, w in windows.items()},
        starts={bid: w["start"] for bid, w in windows.items()},
        total_seconds=float(manifest.get("duration_seconds") or 0.0), timeline=pack.get("timeline") or [])


def beats_for_headlines(ev: AL.Evidence) -> list[dict]:
    return [{"id": b["id"], "summary": (b.get("summary") or "")[:220],
             "told": [ev.facts[f]["claim"][:90] for f in (b.get("reveals") or [])[:3] if f in ev.facts]}
            for b in ev.beats]


async def build_offers(db: Session, case: Case, bp_row: EditorialBlueprint, needs_plan: VisualPlan,
                       manifest: dict, writer: CP.Copywriter | None = None) -> tuple[list[AL.Offer], AL.Evidence]:
    ev = gather(db, case, bp_row, needs_plan, manifest)
    cache = CP.cache_of(needs_plan)
    writer = writer or CP.Copywriter(db, case, cache)
    cfg = T.config().planner["copy"]
    structured = await writer.conflicts(ev.contradictions, ev.sources)
    wanted = [{"id": fid, "claim": f["claim"], "max_chars": cfg["caption_chars"]} for fid, f in ev.facts.items()
              if not f.get("disputed") and not f.get("uncertain") and AL.extract(f["claim"])]
    wanted += [{"id": fid, "claim": f["claim"], "max_chars": cfg["fact_line_chars"]} for fid, f in ev.facts.items()
               if not f.get("disputed") and not f.get("uncertain") and f.get("evidence_strength") in
               T.config().planner["confirmed_strengths"] and not AL.extract(f["claim"])]
    wanted += [{"id": e["id"], "claim": e["claim"], "max_chars": cfg["event_label_chars"]} for e in ev.events
               if len(((e.get("label") or {}).get("en")) or "") > cfg["event_label_chars"]]
    wanted += [{"id": q["id"], "claim": q["question"], "max_chars": cfg["fact_line_chars"]} for q in ev.questions]
    lines = await writer.lines(wanted)
    lines.update({c["id"]: c["summary"] for c in structured})
    confirmed = T.config().planner["confirmed_strengths"]
    sentences = await writer.sentences(
        [{"id": fid, "claim": f["claim"], "max_chars": cfg["statement_chars"]} for fid, f in ev.facts.items()
         if not f.get("disputed") and not f.get("uncertain") and f.get("evidence_strength") in confirmed])
    placed: dict[str, dict] = {}
    beats_view = beats_for_headlines(ev)
    for data in HL.candidates(ev.sources)[:T.config().planner["headline"]["max_per_film"] * 2]:
        key = HL.placement_key(data)
        where = await writer.headline_where(data, key, beats_view, ev.order)
        if where:
            placed[key] = where
    CP.save_cache(db, needs_plan, writer.cache)
    conflicts = AL.conflict_offers(ev, structured)
    best = {o.data["conflict"]["id"]: o.best for o in conflicts}
    quotes = AL.quote_offers(ev)
    facts = AL.key_fact_offers(ev, lines)
    shown = {i for o in quotes + facts for i in o.items}
    headlines = AL.headline_offers(ev, placed)[:T.config().planner["headline"]["max_per_film"]]
    engines = await EN.build_engine_offers(db, case, needs_plan, ev)
    offers = (engines + conflicts + quotes + facts + AL.fact_card_offers(ev, sentences) + AL.timeline_offers(ev, lines)
              + headlines + AL.recap_offers(ev, lines, best, shown))
    return offers, ev


TEXT_FIELDS = {
    "conflict": [("conflict", "title"), ("conflict", "summary"), ("sides", "who"), ("sides", "claim"),
                 ("sides", "source")],
    "conflict_board": [("cards", "title"), ("cards", "a"), ("cards", "b")],
    "recap": [("confirmed", "text"), ("disputed", "text"), ("unknown", "text")],
    "timeline": [("events", "label")],
    "key_facts": [("facts", "caption")],
    "quote": [("", "text")],
    "fact_card": [("facts", "text")],
}


def collect_texts(data: dict, kind: str) -> list[tuple[dict, str]]:
    """(holder, key) of every text of a graphic's data that has to be in the film's language."""
    out = []
    if kind in GS.ENGINE_KINDS:
        return kit.texts_of(data)
    for group, key in TEXT_FIELDS.get(kind, []):
        for holder in ([data] if not group else (data[group] if isinstance(data.get(group), list) else [data[group]])):
            if holder.get(key):
                out.append((holder, key))
    return out


async def localize_graphics(db: Session, case: Case, plan_row: VisualPlan, language: str) -> dict:
    """The text of every placed graphic in the film's language (the app's language pipeline, once per text);
    names, numbers, dates, headlines and sources stay as they are. English needs nothing."""
    from app.documentary.visuals.generated import localize_texts

    plan = json.loads(plan_row.plan_json or "{}")
    if language == "en":
        return {"localized": 0}
    pending: dict[str, str] = {}
    refs: dict[str, tuple[dict, str]] = {}
    for beat in plan.get("beats") or []:
        for shot in beat["shots"]:
            zoom = shot.get("map_zoom")
            if zoom and zoom.get("names") and zoom.get("names_language") != language:
                for n, name in enumerate(zoom["names"]):
                    ref = f"g{len(pending)}"
                    pending[ref] = name
                    refs[ref] = (zoom["names"], n)
                zoom["names_language"] = language
            spec = shot.get("graphic")
            if not spec or spec.get("language") == language:
                continue
            for holder, key in collect_texts(spec["data"], spec["type"]):
                ref = f"g{len(pending)}"
                pending[ref] = holder[key]
                refs[ref] = (holder, key)
            spec["language"] = language
    done = await localize_texts(db, case.id, pending, language) if pending else {}
    for ref, (holder, key) in refs.items():
        holder[key] = done[ref]
    plan_row.plan_json = json.dumps(plan, ensure_ascii=False)
    db.commit()
    return {"localized": len(pending)}
