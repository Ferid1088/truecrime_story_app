"""Prompt store: every prompt lives in `prompts/` as a plain text file, never in code.

        SYSTEM = prompt("naming/case_naming_agent")        # prompts/naming/case_naming_agent.md
    text = prompt("naming/native_title_critic").format(language="German")

A prompt file is exactly the text sent to the model (str.format placeholders
and doubled braces for literal JSON braces keep working). Lines starting with
`#!` at the top are comments (purpose, inputs, output) and are not sent.
Prompts are read once and cached; `reload()` re-reads them (editing a prompt
needs no code change)."""

from __future__ import annotations


from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
SUFFIX = ".md"


class PromptNotFound(KeyError):
    pass


def _strip_comments(text: str) -> str:
    lines = text.split("\n")
    i = 0
    while i < len(lines) and lines[i].startswith("#!"):
        i += 1
    return "\n".join(lines[i:])


@lru_cache(maxsize=None)
def prompt(name: str) -> str:
    path = (PROMPT_DIR / (name + SUFFIX)).resolve()
    if PROMPT_DIR.resolve() not in path.parents:
        raise PromptNotFound(f"invalid prompt name {name!r}")
    try:
        return _strip_comments(path.read_text(encoding="utf-8")).rstrip("\n")
    except FileNotFoundError:
        raise PromptNotFound(f"prompt {name!r} not found ({path})") from None


def reload() -> None:
    prompt.cache_clear()


def available() -> list[str]:
    return sorted(str(p.relative_to(PROMPT_DIR).with_suffix("")).replace("\\", "/")
                  for p in PROMPT_DIR.rglob("*" + SUFFIX))
