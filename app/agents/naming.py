"""The title agent: it proposes native titles and scores them itself (one request, checked once more by the same model; there is no separate critic).
Prompt: prompts/agents/case_naming_agent.md."""

from __future__ import annotations

from app.agents.base import Agent
from app.core.ai_config import ai_config

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
            "limits": {"words": [cfg.min_words, cfg.max_words], "characters": min(cfg.max_chars, title_budget(language))}})
        return {"editorial_concept": str(data.get("editorial_concept") or "").strip()[:300] or None,
                "candidates": [c for c in data.get("candidates", [])
                               if isinstance(c, dict) and c.get("title")]}

    async def score(self, *, language: str, case: dict, titles: list[str]) -> list[dict]:
        """The measures of titles a person wrote (the same ones the model gives its own candidates)."""
        data = await self.ask({"language": LANG_NAMES.get(language, language), "case": case, "score": titles})
        return [s for s in data.get("scores", []) if isinstance(s, dict)]
