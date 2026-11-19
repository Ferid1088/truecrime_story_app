"""The two shared creative standards, each put in front of the prompt of every agent that uses it, with every example:

1. titles, Shorts/Reels titles, captions, tags and cover text (prompts/agents/title_standard.md and the 630 patterns of prompts/agents/title_library.json; config title_standard.agents);
2. Shorts/Reels hooks and narration, also the avatar's opening narration (prompts/agents/shorts_standard.md and the 144 complete scripts of prompts/agents/shorts_library.json;
   config shorts_standard.agents).

The text is the same for every call of a standard, so the provider can cache it."""

from __future__ import annotations

import json
from functools import lru_cache

from app.core.ai_config import ai_config
from app.core.prompts import PROMPT_DIR, prompt

LANGUAGE_NAMES = {"fa": "Persian (Farsi)", "en": "English", "de": "German", "es": "Spanish", "tr": "Turkish", "fr": "French", "it": "Italian", "pt": "Brazilian Portuguese", "ar": "Arabic"}
CLOSING = "FINAL REMINDER: your whole answer is the single JSON object described in your task above (valid JSON, nothing before or after it). Never answer with a numbered list, prose or commentary."
TASK_MARK = "=== YOUR TASK AND THE ANSWER FORMAT YOU MUST FOLLOW (the library above is reference material, never your answer's shape) ==="


def _load(name: str) -> dict:
    return json.loads((PROMPT_DIR / "agents" / name).read_text(encoding="utf-8"))["library"]


@lru_cache(maxsize=1)
def library() -> dict[str, dict[str, list[str]]]:
    """The 630 title patterns: language -> category -> ten titles."""
    return _load("title_library.json")


@lru_cache(maxsize=1)
def shorts_library() -> dict[str, dict[str, list[dict]]]:
    """The 144 complete Shorts scripts: language -> category -> two {title, text}."""
    return _load("shorts_library.json")


@lru_cache(maxsize=1)
def standard_text() -> str:
    """The titles standard and every example of every language, in one stable text."""
    parts = ["SHARED CREATIVE STANDARD FOR TITLES, SHORTS/REELS TITLES, CAPTIONS, TAGS AND COVER TEXT (applies to your task whatever your role)", prompt("agents/title_standard"),
             "THE REFERENCE LIBRARY, ALL EXAMPLES: learn intensity, rhythm and variety from the language you write or judge; never copy a pattern whose premise the case does not support."]
    for code, categories in library().items():
        parts.append(f"=== {code.upper()} {LANGUAGE_NAMES.get(code, code)} ===")
        for category, examples in categories.items():
            parts.append(category + "\n" + "\n".join(f"{n}. {text}" for n, text in enumerate(examples, 1)))
    return "\n\n".join(parts)


@lru_cache(maxsize=1)
def shorts_text() -> str:
    """The Shorts standard and all 144 scripts, in one stable text."""
    parts = ["SHARED CREATIVE STANDARD FOR SHORTS/REELS HOOKS AND NARRATION (applies to your task whatever your role)", prompt("agents/shorts_standard"),
             "THE EXAMPLE LIBRARY, ALL 144 SCRIPTS: fictional demonstrations of intensity, specificity and pacing, never case sources."]
    for code, categories in shorts_library().items():
        parts.append(f"=== {code.upper()} {LANGUAGE_NAMES.get(code, code)} ===")
        for category, examples in categories.items():
            parts.append(category + "\n" + "\n\n".join(f"{e['title']}\n{e['text']}" for e in examples))
    return "\n\n".join(parts)


def with_standard(agent: str, system: str) -> str:
    """The standards of the agent (each of the lists in config), then its own prompt. The task comes last so that its answer format (JSON) is the last thing the model reads: after
    a long library a model answers in lists."""
    texts = [text() for text, agents in ((standard_text, ai_config.title_standard.agents), (shorts_text, ai_config.shorts_standard.agents)) if agent in agents]
    return "\n\n".join([*texts, TASK_MARK, system, CLOSING]) if texts else system
