"""Generated visuals the plan asks for — maps and document cards — become
ordinary library assets (provider "generated"), so the renderer only
ever sees files. Text that appears on screen (dates, quotes, place
names, labels) is localized per language at production time.
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import Case, Source, VisualAsset, VisualPlan
from app.documentary import storage
from app.documentary.visuals import images as IM
from app.documentary.visuals import maps as MAPS
from app.documentary.visuals import tiers as T
from app.documentary.visuals.research import next_asset_code
from app.documentary.visuals.typography import document_card
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run


def _find_generated(db: Session, case_id: int, asset_type: str, spec_key: str) -> VisualAsset | None:
    for a in db.query(VisualAsset).filter(
            VisualAsset.case_id == case_id, VisualAsset.provider == "generated",
            VisualAsset.asset_type == asset_type).all():
        if (json.loads(a.spec_json or "{}")).get("key") == spec_key:
            return a
    return None


async def _case_anchor(db: Session, case: Case, place: str) -> tuple[float, float] | None:
    """Where the case happens: the first place of the case that was
    mapped before (so an ambiguous name resolves near it)."""
    first = (db.query(VisualAsset)
             .filter(VisualAsset.case_id == case.id, VisualAsset.asset_type == "map",
                     VisualAsset.location.isnot(None), VisualAsset.location != place)
             .order_by(VisualAsset.id).first())
    if first is None:
        return None
    geo = await MAPS.geocode(first.location)
    return (geo["lat"], geo["lon"]) if geo else None


def _map_tier(a: VisualAsset) -> None:
    """Library fields of a map (also for maps made before tiers existed)."""
    if a.relevance_tier is None:
        tier = T.provisional_tier("generated", "context", "place")
        a.relevance_tier, a.case_relevance = tier, T.tier_label(tier)
        a.entity_type = a.entity_type or "place"
        a.found_during = a.found_during or "generated"


def _cached_chain(db: Session, case: Case, place: str) -> list[VisualAsset]:
    """The zoom sequence rendered earlier for this place (the latest
    chain), when the geocoder cannot be asked again."""
    prefix = f"map|{place.lower()}|"
    found = {}
    latest: list[int] = []
    for a in db.query(VisualAsset).filter(
            VisualAsset.case_id == case.id, VisualAsset.provider == "generated",
            VisualAsset.asset_type == "map").order_by(VisualAsset.id).all():
        spec = json.loads(a.spec_json or "{}")
        if str(spec.get("key") or "").startswith(prefix):
            found[spec.get("zoom")] = a
            latest = spec.get("zooms") or latest
    if latest and all(z in found for z in latest):
        return [found[z] for z in latest]
    return [found[z] for z in sorted(found, key=lambda z: (z is None, z or 0))]


async def _previous_point(previous) -> tuple[float, float] | None:
    if isinstance(previous, str):
        geo = await MAPS.geocode(previous)
        return (geo["lat"], geo["lon"]) if geo else None
    return MAPS.as_point(previous)


async def map_chain(db: Session, case: Case, place: str, previous=None) -> dict | None:
    """The map sequence of one place: one library asset per zoom of its
    zoom chain (wide -> close), chosen by the place's granularity and,
    when `previous` (the film's previously mapped place: {"lat","lon"},
    (lat, lon) or a place name) is nearby, starting at city level.

    Returns {"assets", "zooms", "granularity", "bbox", "near_previous"}
    or None when the place cannot be geocoded and was never mapped.
    Each zoom of a place is rendered once per case and reused."""
    prev = await _previous_point(previous)
    near = prev or await _case_anchor(db, case, place)
    geo = await MAPS.geocode(place, near=near)
    if not geo:
        cached = _cached_chain(db, case, place)
        if not cached:
            return None
        spec = json.loads(cached[-1].spec_json or "{}")
        return {"assets": cached, "zooms": [json.loads(a.spec_json or "{}").get("zoom")
                                             for a in cached],
                "granularity": spec.get("granularity"), "bbox": spec.get("bbox"),
                "near_previous": False}
    chain = MAPS.zoom_chain(geo, prev)
    gran = geo.get("granularity") or "unknown"
    assets: list[VisualAsset] = []
    for z in chain:
        key = f"map|{place.lower()}|{z}"
        a = _find_generated(db, case.id, "map", key)
        if a is None:
            code = next_asset_code(db)
            out = storage.visuals_dir(case.id, "map") / f"{code}.jpg"
            info = await MAPS.render_map(geo["lat"], geo["lon"], z, out)
            thumb = storage.thumbs_dir(case.id) / f"{code}.jpg"
            img = IM.open_image(out.read_bytes())
            IM.save_thumbnail(img, thumb)
            tier, why = T.provisional_why("generated", "context", "place")
            a = VisualAsset(
                case_id=case.id, asset_code=code, asset_type="map", subject_type="geography",
                title=f"Map: {place} (zoom {z})", description=geo.get("display_name"),
                location=place, provider="generated", asset_role="context",
                rights_status="owned", rights_reason="generated from OpenStreetMap data",
                credit=ai_config.maps.attribution, license="ODbL (map data)",
                verification_status="verified", verification_confidence=1.0,
                local_path=storage.rel(out), thumbnail_path=storage.rel(thumb),
                width=img.width, height=img.height,
                relevance_tier=tier, case_relevance=T.tier_label(tier),
                entity_type="place", found_during="generated",
                spec_json=json.dumps({"key": key, "place": place, "zoom": z,
                                      "zooms": chain, "granularity": gran,
                                      "bbox": geo.get("bbox"),
                                      "lat": geo["lat"], "lon": geo["lon"],
                                      "marker": info["marker"], "tier_reason": why}),
            )
            db.add(a)
        else:
            _map_tier(a)
        db.commit()
        assets.append(a)
    return {"assets": assets, "zooms": chain, "granularity": gran, "bbox": geo.get("bbox"),
            "near_previous": MAPS.is_nearby(geo, prev)}


async def ensure_map(db: Session, case: Case, place: str, previous=None) -> list[str]:
    """Asset codes of the zoom sequence for a place (wide -> close); see
    map_chain. `previous` is optional (the film's previously mapped place)."""
    info = await map_chain(db, case, place, previous)
    return [a.asset_code for a in info["assets"]] if info else []


def ensure_document(db: Session, case: Case, source: Source, passage: str) -> VisualAsset:
    key = f"doc|{source.id}|{passage[:120].lower()}"
    a = _find_generated(db, case.id, "document", key)
    if a:
        return a
    code = next_asset_code(db)
    img, bbox = document_card(source.title or "", source.publisher, passage,
                              source.raw_text or passage)
    out = storage.visuals_dir(case.id, "document") / f"{code}.jpg"
    img.save(out, "JPEG", quality=92)
    thumb = storage.thumbs_dir(case.id) / f"{code}.jpg"
    IM.save_thumbnail(img, thumb)
    # a card quoting a case source is case material (tier 1): repetition
    # control counts it as evidence, the audit can say why
    tier, why = T.provisional_why("generated", "evidence", "document")
    a = VisualAsset(
        case_id=case.id, asset_code=code, asset_type="document", subject_type="document",
        title=f"Document: {source.title}"[:500], description=passage[:1000],
        provider="generated", asset_role="evidence", source_url=source.url,
        source_name=source.publisher or source.title,
        rights_status="editorial_review_required",
        rights_reason="quotes a passage of a case source (short quotation)",
        verification_status="verified", verification_confidence=1.0,
        local_path=storage.rel(out), thumbnail_path=storage.rel(thumb),
        width=img.width, height=img.height,
        relevance_tier=tier, case_relevance=T.tier_label(tier),
        entity_type="document", found_during="generated",
        spec_json=json.dumps({"key": key, "source_id": source.id, "passage": passage,
                              "highlight": list(bbox), "tier_reason": why}),
    )
    db.add(a)
    db.commit()
    return a


def attach_overlays(plan: dict, requirements: dict) -> dict:
    """English source text of every on-screen text (localized later). A
    map names the place it shows (the shot's own place — a place the
    sentence named — else the beat's map place)."""
    reqs = {b["beat_id"]: b for b in requirements.get("beats") or []}
    for pb in plan.get("beats") or []:
        req = reqs.get(pb["beat_id"], {})
        for s in pb["shots"]:
            cmd = s["command"]
            place = s.get("map_place") or req.get("map_place")
            if cmd == "SHOW_MAP" and place:
                s["overlay"] = {"kind": "place", "text_en": place.split(",")[0]}
            elif cmd == "SHOW_QUOTE" and req.get("quote"):
                s["overlay"] = {"kind": "quote", "text_en": req["quote"]["text"],
                                "fact_id": req["quote"]["fact_id"]}
            elif cmd == "SHOW_DATE" and req.get("date_text"):
                s["overlay"] = {"kind": "date", "text_en": req["date_text"]}
    return plan


async def materialize(db: Session, case: Case, plan_row: VisualPlan) -> dict:
    """Create the maps/document cards the plan uses; attach their asset
    codes and the English source text of every on-screen text."""
    plan = json.loads(plan_row.plan_json or "{}")
    reqs = {b["beat_id"]: b for b in json.loads(plan_row.requirements_json or "{}").get("beats", [])}
    stats = {"maps": 0, "documents": 0, "dropped": 0}
    # the film's previously mapped place: a nearby next map starts at city
    # level (the viewer already knows the country)
    previous: dict | None = None
    for pb in plan.get("beats") or []:
        req = reqs.get(pb["beat_id"], {})
        keep = []
        for s in pb["shots"]:
            cmd = s["command"]
            if cmd == "SHOW_MAP":
                place = s.get("map_place") or req.get("map_place")
                info = await map_chain(db, case, place, previous) if place else None
                if not info:
                    stats["dropped"] += 1
                    s.update({"command": "KEEP_CURRENT_IMAGE", "motion": "CONTINUE",
                              "why": "map unavailable (geocoding failed)"})
                    s.pop("overlay", None)
                else:
                    s["map_assets"] = [a.asset_code for a in info["assets"]]
                    s["map_info"] = {
                        "place": place, "zooms": info["zooms"],
                        "granularity": info["granularity"],
                        "after_place": (previous or {}).get("place"),
                        "starts_at_city": bool(info["near_previous"]),
                    }
                    spec = json.loads(info["assets"][-1].spec_json or "{}")
                    if spec.get("lat") is not None:
                        previous = {"place": place, "lat": spec["lat"], "lon": spec["lon"]}
                    stats["maps"] += 1
            elif cmd == "SHOW_DOCUMENT":
                doc = req["document"]
                src = db.get(Source, doc["source_id"])
                if src is None:
                    stats["dropped"] += 1
                    continue
                a = ensure_document(db, case, src, doc["passage"])
                s["asset_id"] = a.asset_code
                stats["documents"] += 1
            keep.append(s)
        pb["shots"] = keep or pb["shots"]
    plan_row.plan_json = json.dumps(plan, ensure_ascii=False)
    db.commit()
    return stats


# ---------------------------------------------------------------------------
# on-screen text per language
# ---------------------------------------------------------------------------

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
    "fa": ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت",
           "سپتامبر", "اکتبر", "نوامبر", "دسامبر"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس",
           "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
}
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def format_date(text: str, language: str) -> str | None:
    """'16 July 2007' / 'July 16, 2007' / 'July 2007' / '2007' -> localized.
    None when the text is not a plain date (then the localizer handles it)."""
    t = " ".join(text.replace(",", " ").split())
    months = {m.lower(): i for i, m in enumerate(MONTHS["en"])}
    day = month = year = None
    for tok in t.split():
        low = tok.lower().rstrip(".")
        low = re.sub(r"(st|nd|rd|th)$", "", low) if low[:1].isdigit() else low
        if low in months:
            month = months[low]
        elif low.isdigit() and len(low) == 4:
            year = low
        elif low.isdigit() and 1 <= int(low) <= 31:
            day = int(low)
        elif low not in ("of", "the", "on"):
            return None
    if year is None:
        return None
    m = MONTHS[language if language in MONTHS else "en"]
    if language == "de":
        out = " ".join(filter(None, [f"{day}." if day else None,
                                     m[month] if month is not None else None, year]))
    elif language in ("fa", "ar"):
        out = " ".join(filter(None, [str(day) if day else None,
                                     m[month] if month is not None else None, year]))
        if language == "fa":
            out = out.translate(_FA_DIGITS)
    else:
        out = " ".join(filter(None, [str(day) if day else None,
                                     m[month] if month is not None else None, year]))
    return out


LABELS = {
    "illustration": {"en": "Illustrative image", "de": "Symbolbild",
                     "fa": "تصویر نمادین", "ar": "صورة تعبيرية"},
    "translated_quote": {"en": "", "de": "Übersetzung", "fa": "ترجمه", "ar": "ترجمة"},
}

LOCALIZER_SYSTEM = """
You localize short on-screen texts of a documentary into {name}.
- Place names: the spelling a {name} documentary uses; if the narration
  excerpt contains the name, use exactly that spelling.
- Quotes: a faithful translation of the real words (no paraphrase, no
  added meaning); keep it short.
- Keep it as short as the original. No quotation marks (added later).
Return JSON only: {{"texts": {{"<id>": "<localized text>"}}}}
"""


async def localize_texts(db: Session, case_id: int, items: dict[str, str],
                         language: str, narration_excerpt: str = "") -> dict[str, str]:
    """{id: english} -> {id: localized}. Dates are formatted
    deterministically; everything else goes to role overlay_localizer."""
    out, todo = {}, {}
    for k, v in items.items():
        if language == "en":
            out[k] = v
            continue
        d = format_date(v, language) if k.startswith("date") else None
        if d:
            out[k] = d
        else:
            todo[k] = v
    if todo:
        from app.documentary.spoken import LANG_NAMES

        gen = get_generation_provider()
        with track_run(db, case_id, f"Overlay Localizer ({language})",
                       input_summary=f"{len(todo)} texts") as run:
            data, res = await gen.generate_structured(
                "overlay_localizer",
                LOCALIZER_SYSTEM.format(name=LANG_NAMES.get(language, language)),
                json.dumps({"language": language, "texts": todo,
                            "narration_excerpt": narration_excerpt[:3000]},
                           ensure_ascii=False))
            stamp_run(run, res, "overlay_localizer")
        texts = (data or {}).get("texts") or {}
        for k, v in todo.items():
            t = texts.get(k)
            out[k] = str(t).strip().strip("\"«»“”„") if t else v
    return out
