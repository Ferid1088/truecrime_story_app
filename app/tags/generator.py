"""The tag generator: one model call per case and language on the case dossier (facts it may cite, never the script's wording), then the deterministic checks of
`validate`. A list with errors is sent back once with its problems (`tags.repairs`); what is still wrong stays in the report, it is not hidden. The model's own
counts are never used: the app counts."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.models import Case
from app.providers.generation import get_generation_provider
from app.services.tracking import stamp_run, track_run
from app.tags import dossier as D
from app.tags import validate as V


class TagError(RuntimeError):
    """The generator could not produce a usable list (no answer, no tags)."""


def parse(raw: object, dossier: dict) -> tuple[list[V.Tag], list[str], list[str]]:
    """(tags, warnings, structure problems) of the model's answer. A tag without text is skipped; a wrong case or language is a problem to send back."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return [], [], ["the answer is not a JSON object"]
    if raw.get("case_id") != dossier["case_id"]:
        problems.append(f"case_id must be {dossier['case_id']}")
    if raw.get("language") != dossier["language"]:
        problems.append(f"language must be {dossier['language']}")
    items = raw.get("tags")
    if not isinstance(items, list):
        return [], [], problems + ["tags must be a list"]
    tags = []
    for item in items:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            problems.append("every tag is an object with a text")
            continue
        evidence = item.get("evidence")
        tags.append(V.Tag(item["text"], str(item.get("category") or "manual"), [str(e) for e in evidence] if isinstance(evidence, list) else []))
    warnings = [str(w) for w in raw.get("warnings") or [] if isinstance(w, str)]
    return tags, warnings, problems


class TagGenerator:
    def __init__(self):
        self.gen = get_generation_provider()
        self.cfg = ai_config.tags_settings()

    async def generate(self, db: Session, case: Case, language: str) -> dict:
        """{tags, warnings, fixes, report, fingerprint, model}: a checked list. Raises TagError when no tags can be read."""
        dossier = D.build(db, case, language)
        view = D.model_view(dossier, D.approved_english(db, case.id), json.loads(prompt("tags/reference_examples")))

        async def ask(extra: dict):
            with track_run(db, case.id, f"Tag Generator ({language})", input_summary=language) as run:
                raw, res = await run_agent("tag_generator", self.gen, json.dumps({**view, **extra}, ensure_ascii=False))
                stamp_run(run, res, ai_config.agents["tag_generator"].role)
            return raw, res

        extra: dict = {}
        for attempt in range(self.cfg.repairs + 1):
            raw, res = await ask(extra)
            tags, warnings, structure = parse(raw, dossier)
            tags, fixes = V.repair(tags)
            if not structure:
                tags, trimmed = V.trim_to_fit(tags, self.cfg.max_chars)
                fixes += trimmed
            report = V.check(tags, dossier, self.cfg)
            errors = [i["message"] + (f" ({i['tag']})" if i.get("tag") else "") for i in report["issues"] if i["severity"] == "error" and i["type"] != "too_long"]
            if (not structure and not errors) or attempt == self.cfg.repairs:
                break
            extra = {"previous": raw, "problems_to_fix": structure + errors}
        if not tags:
            raise TagError("the generator returned no usable tag" + (f": {'; '.join(structure)}" if structure else ""))
        return {"tags": tags, "warnings": warnings, "fixes": fixes, "report": report, "fingerprint": dossier["fingerprint"], "model": getattr(res, "model", None)}
