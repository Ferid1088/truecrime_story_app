"""How a language's narration writes the names of the case's entities.

The planner's rules that find a named person or place (the first mention, a photo of the person named, a picture that comes again) match
the entity's slug in the text. That works for the languages written in Latin script; a Persian or Arabic narration writes the same name in
its own script. For those languages (`names.native_script_languages`) the forms are read from the narration once by an agent, each form is
checked to occur in the text (a form the text does not contain is dropped), and the table `entity_name_forms` keeps them for the film."""

from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, EntityNameForm
from app.documentary.production.picture_rules import NameForms
from app.language_pipeline.glossary import fold
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.budget import BudgetService
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)
PARTS = ("full", "first", "last")


def uses_native_script(language: str) -> bool:
    return language in ai_config.names.native_script_languages


def narration_of(sentences: list[dict]) -> str:
    return " ".join((s.get("display") or s.get("speech") or "") for s in sorted(sentences, key=lambda s: s["start"]))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def prefixes_of(language: str) -> list[str]:
    return list(ai_config.language_pipeline.cross_language.glossary.attached_prefixes.get(language) or [])


def load(db: Session, case_id: int, language: str) -> NameForms:
    """The forms stored for this language (none for a Latin-script language: its names are matched by slug)."""
    if not uses_native_script(language):
        return NameForms()
    rows = db.query(EntityNameForm).filter(EntityNameForm.case_id == case_id, EntityNameForm.language == language).all()
    return NameForms({r.entity_key: json.loads(r.forms_json or "{}") for r in rows}, prefixes_of(language))


def verified(answer, entities: list[dict], narration: str) -> dict[str, dict[str, list[str]]]:
    """The agent's forms per entity key, only those the narration really contains (folded) and long enough to be a name."""
    low = fold(narration)
    minimum = ai_config.names.min_form_chars
    keys = {e["key"] for e in entities}
    out: dict[str, dict[str, list[str]]] = {}
    for item in answer.get("entities") or [] if isinstance(answer, dict) else []:
        if not isinstance(item, dict) or item.get("key") not in keys:
            continue
        parts = {}
        for part in PARTS:
            found = [f.strip() for f in item.get(part) or [] if isinstance(f, str) and len(fold(f)) >= minimum and fold(f) in low]
            parts[part] = list(dict.fromkeys(found))
        out[item["key"]] = parts
    return out


async def ensure(db: Session, case: Case, language: str, sentences: list[dict], entities: list[dict]) -> dict:
    """Reads the forms for this narration when the stored ones are for another text; a Latin-script language needs none.
    Returns {"read": the entities with a form found, "missing": the entities the text does not name, "reused": bool}."""
    if not uses_native_script(language):
        return {"skipped": True}
    narration = narration_of(sentences)
    digest = text_hash(narration)
    have = {r.entity_key: r for r in db.query(EntityNameForm).filter(
        EntityNameForm.case_id == case.id, EntityNameForm.language == language).all()}
    if entities and all(k["key"] in have and have[k["key"]].text_hash == digest for k in entities):
        return {"reused": True, "read": len(have)}
    snapshot = BudgetService(db).snapshot(case.id)
    if snapshot.spent_eur >= snapshot.cap_eur - 1e-9:
        return {"error": "budget cap reached before the name forms were read"}
    payload = {"language": language, "entities": [{k: e.get(k) for k in ("key", "name", "type") if e.get(k)} for e in entities],
               "narration": narration}
    try:
        with track_run(db, case.id, "Name forms reader", input_summary=f"{language}: {len(entities)} entities") as run:
            data, res = await run_agent("documentary.name_forms", get_generation_provider(), json.dumps(payload, ensure_ascii=False))
            stamp_run(run, res, "name_forms_reader")
    except GenerationError as e:
        log.warning("name forms for %s failed: %s", language, type(e).__name__)
        return {"error": f"{type(e).__name__}: {e}"[:300]}
    forms = verified(data, entities, narration)
    for entity in entities:                                  # an entity the text does not name has its (empty) row too: the text is read once
        parts = forms.get(entity["key"]) or {part: [] for part in PARTS}
        row = have.get(entity["key"])
        if row is None:
            row = EntityNameForm(case_id=case.id, language=language, entity_key=entity["key"], text_hash=digest)
            db.add(row)
        row.text_hash, row.forms_json = digest, json.dumps(parts, ensure_ascii=False)
    db.commit()
    named = [k for k, p in forms.items() if any(p.values())]
    return {"read": len(named), "missing": sorted(set(e["key"] for e in entities) - set(named))}
