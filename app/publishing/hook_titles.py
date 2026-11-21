"""The titles of the Shorts, written to the owner's title standard (prompts/publishing/title_standard.md: the rules and, per language, 70 example hooks in seven categories). A title is a hook: an open
loop the Short answers, built only on what the Short says, in the natural words of the language. For one Short it asks GPT-6 for three different candidates, a winner and a backup; names of the people are
allowed in a title. It is asked for on request (one request per Short, booked to the case's ledger); the winner can be written into the Short, and for a language the owner reviews
(covers.text_review_languages) that change still waits for his release."""

from __future__ import annotations

import json
import re

from app.agents.shorts import CreativePack
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.publishing import filenames
from app.services.tracking import stamp_run, track_run
from app.shortform import clips as C

_SECTION = re.compile(r"^=+ ([A-Z]{2})(?:-[A-Z]{2})? —", re.M)


def standard_for(language: str) -> str:
    """The standard's rules (everything before its reference library) and the language's own section of examples."""
    text = prompt("publishing/title_standard")
    rules = text[:text.index("REFERENCE LIBRARY")]
    marks = list(_SECTION.finditer(text))
    code = language.upper()
    for i, mark in enumerate(marks):
        if mark.group(1) == code:
            end = marks[i + 1].start() if i + 1 < len(marks) else text.index("END OF REFERENCE LIBRARY")
            return f"{rules}\n{text[mark.start():end]}"
    raise KeyError(f"the title standard has no section for {language}")


async def write(gen, db, case, language: str, clip_key: str, *, max_characters: int | None = None) -> dict:
    """{candidates: [{title, angle, why}], winner, backup} for one Short; one GPT-6 request, booked to the case."""
    row = next((r for r in C.clips_of(db, case.id, language) if r.clip_key == clip_key), None)
    if row is None:
        raise KeyError(f"there is no {language} Short {clip_key}")
    script = json.loads(row.script_json or "{}")
    limit = max_characters or ai_config.creative_pack.short_title_max_characters
    given = {"language": language, "film_title": filenames.film_title_of(db, case, language), "max_characters": limit,
             "short": {"hook": script.get("hook", ""), "story": script.get("story", ""), "avatar": script.get("avatar", ""), "what_happened": (script.get("concept") or {}).get("anchor")}}
    with track_run(db, case.id, f"Hook titles: {language} {clip_key}") as run:
        data, result = await CreativePack(gen).run(system=f"{standard_for(language)}\n\n{prompt('publishing/title_task')}", dossier=given)
        stamp_run(run, result, "creative_pack")
    found = [c for c in (data.get("candidates") or []) if isinstance(c, dict) and str(c.get("title") or "").strip()]
    found = [{**c, "title": " ".join(str(c["title"]).split())} for c in found if len(" ".join(str(c["title"]).split())) <= limit]
    if not found:
        raise ValueError("the model gave no usable title")
    pick = lambda key, fallback: data.get(key) if isinstance(data.get(key), int) and 0 <= data[key] < len(found) else fallback           # noqa: E731
    return {"candidates": found, "winner": pick("winner", 0), "backup": pick("backup", 1 if len(found) > 1 else 0)}
