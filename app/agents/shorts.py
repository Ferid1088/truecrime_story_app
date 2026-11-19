"""Agents that run on the shared standards (config title_standard and shorts_standard): the creative pack (every title, cover text, Short hook and Short script of a case for all languages in one
request) and the cover subject picker (roles and genders of the people a cover may show). Prompts: prompts/agents/creative_pack.md and covers/subject_picker.md."""

from __future__ import annotations

from app.agents.base import Agent

CATEGORIES = ["Desire, affairs and murder", "Beauty and dangerous charm", "Jealousy and revenge", "Killer in the closest circle", "Male charm and forbidden relationships",
              "Chilling cruelty", "Posthumous clues", "The mistress and the hidden plot"]


class CoverSubjectPicker(Agent):
    name = "cover_subject_picker"

    async def run(self, *, case: dict, people: list[dict]) -> list[dict]:
        data = await self.ask({"case": case, "people": people})
        return [p for p in (data.get("people") or []) if isinstance(p, dict) and p.get("entity_key")]


class CreativePack(Agent):
    name = "creative_pack"

    async def run(self, *, dossier: dict, previous: dict | None = None, problems: dict | None = None) -> dict:
        """One request for the whole pack of a case (all languages); on a repeat call with the earlier answer and what the code found wrong. The answer is returned as the model gave it, normalized
        to the expected containers (validation is the code's, in app/shortform/pack.py)."""
        data = await self.ask({**dossier, **({"previous": previous, "problems_to_fix": problems} if problems else {})})
        scouting = data.get("scouting") if isinstance(data.get("scouting"), dict) else {}
        languages = data.get("languages") if isinstance(data.get("languages"), dict) else {}
        return {"scouting": {"concepts": [c for c in (scouting.get("concepts") or []) if isinstance(c, dict)], "chosen": [c for c in (scouting.get("chosen") or []) if isinstance(c, dict)],
                             "runner_ups": [i for i in (scouting.get("runner_ups") or []) if isinstance(i, int)]},
                "languages": {code: body for code, body in languages.items() if isinstance(body, dict)},
                "checks": data.get("checks") if isinstance(data.get("checks"), dict) else {}, "flags": [str(f)[:300] for f in (data.get("flags") or []) if f]}
