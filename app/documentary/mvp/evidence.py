"""A placed graphic's text in the film's language: the text of every card the toolkit fixed in the film is put into the
language of the film (names, numbers, dates, headlines and sources stay as they are)."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import Case, VisualPlan
from app.documentary.graphics import shots as GS
from app.documentary.graphics.engines import kit

TEXT_FIELDS = {
    "conflict": [("conflict", "title"), ("conflict", "summary"), ("sides", "who"), ("sides", "claim"),
                 ("sides", "source")],
    "conflict_board": [("cards", "title"), ("cards", "a"), ("cards", "b")],
    "recap": [("confirmed", "text"), ("disputed", "text"), ("unknown", "text")],
    "key_facts": [("facts", "caption")],
    "quote": [("", "text")],
    "fact_card": [("facts", "text")],
}


def collect_texts(data: dict, kind: str) -> list[tuple[dict, str]]:
    """(holder, key) of every text of a graphic's data that has to be in the film's language."""
    out = []
    if kind in GS.ENGINE_KINDS:
        return kit.texts_of(data)
    if kind == "timeline":
        return [(point["labels"], n) for point in data.get("points") or [] for n in range(len(point.get("labels") or []))]
    for group, key in TEXT_FIELDS.get(kind, []):
        found = data.get(group) if group else data
        for holder in ([] if found is None else found if isinstance(found, list) else [found]):
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
            if zoom and zoom.get("label_language") != language:       # the name on the marker card of the map
                ref = f"g{len(pending)}"
                pending[ref] = zoom["place"]
                refs[ref] = (zoom, "place_label")
                zoom["label_language"] = language
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
    return {"localized": len(pending), "left_in_another_script": left_in_another_script(plan, language)}


def left_in_another_script(plan: dict, language: str) -> list[dict]:
    """The texts of the placed graphics that a language written in its own script still holds in Latin letters (an untranslated
    sentence or name): [{beat, kind, text}]. Empty for a Latin-script language, whose texts cannot be told apart by script."""
    from app.documentary import names as NS

    if not NS.uses_native_script(language):
        return []
    found = []
    for beat in plan.get("beats") or []:
        for shot in beat["shots"]:
            spec = shot.get("graphic")
            if spec and spec.get("language") == language:
                found += [{"beat": beat["beat_id"], "kind": spec["type"], "text": holder[key]}
                          for holder, key in collect_texts(spec["data"], spec["type"]) if NS.LATIN_WORD.search(str(holder[key]))]
    return found
