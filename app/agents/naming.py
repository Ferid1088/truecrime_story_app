"""Title agents: the proposer, the critic and the native-language check.
Prompts: prompts/agents/case_naming_agent.md, case_title_critic.md, native_title_critic.md."""

from __future__ import annotations

import json

from app.agents.base import Agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt

LANG_NAMES = {"en": "English", "de": "German", "fa": "Persian (Farsi)", "ar": "Arabic", "es": "Mexican Spanish",
              "it": "Italian", "fr": "French", "pt": "Brazilian Portuguese", "tr": "Turkish"}


class CaseNamingAgent(Agent):
    name = "case_naming_agent"

    async def run(self, *, language: str, count: int, case: dict, family_concept: str | None,
                  avoid: list[str], other_language_titles: dict) -> dict:
        from app.identity.titles import title_budget

        cfg = ai_config.case_naming
        data = await self.ask({
            "language": LANG_NAMES.get(language, language), "count": count, "case": case,
            "family_concept": family_concept, "avoid": avoid,
            "other_language_titles": other_language_titles,
            "limits": {"words": [cfg.min_words, cfg.max_words], "characters": min(cfg.max_chars, title_budget(language))},
            "example": json.loads(prompt("agents/title_examples")).get(language)})
        return {"editorial_concept": str(data.get("editorial_concept") or "").strip()[:300] or None,
                "candidates": [c for c in data.get("candidates", [])
                               if isinstance(c, dict) and c.get("title")]}


class CaseTitleCritic(Agent):
    name = "case_title_critic"

    async def run(self, *, language: str, case: dict, titles: list[str]) -> list[dict]:
        data = await self.ask({"language": language, "case": case, "titles": titles})
        return [s for s in data.get("scores", []) if isinstance(s, dict)]


class NativeTitleCritic(Agent):
    name = "native_title_critic"

    async def run(self, *, language: str, titles: list[str]) -> list[dict]:
        data = await self.ask({"titles": titles}, language=LANG_NAMES.get(language, language))
        return [s for s in data.get("scores", []) if isinstance(s, dict)]
