"""The creative agents: GPT-6 writes the first version of a case's whole pack (film and Shorts titles, cover texts, Short narrations for all languages) in ONE request, GPT-Luna rewrites it all in
ONE request; the cover subject picker reads the roles and genders of the people a cover may show. Prompts: prompts/agents/creative_pack.md and creative_polisher.md, each followed by
and prompts/covers/subject_picker.md."""

from __future__ import annotations

import json
import re

from app.agents.base import Agent
from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.providers.generation.json_repair import repair


def extract_json(text: str) -> dict:
    """The JSON object in a model's text answer (a model without structured output may put words or a code fence around it); {} when there is none."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        data = repair(text) or drop_stray_closers(text[start:end + 1]) or salvage_blocks(text)      # free technical repairs only; nothing is guessed
    return data if isinstance(data, dict) else {}


def salvage_blocks(text: str) -> dict | None:
    """What still parses of an answer whose structure is broken somewhere: each of "picks", "speculations" and every language's own block is read on its own and kept only when it parses whole and holds
    its `shorts`; a broken block is left out (the caller keeps GPT-6's wording for it and says so). None when nothing could be read."""
    decoder, found = json.JSONDecoder(), {}
    for key in ("picks", "speculations"):
        if match := re.search(rf'"{key}"\s*:\s*', text):
            try:
                found[key] = decoder.raw_decode(text, match.end())[0]
            except ValueError:
                pass
    languages = {}
    for code in ai_config.creative_pack.languages:
        if match := re.search(rf'"{code}"\s*:\s*\{{', text):
            try:
                block = decoder.raw_decode(text, match.end() - 1)[0]
            except ValueError:
                continue
            if isinstance(block, dict) and isinstance(block.get("shorts"), list):
                languages[code] = block
    if languages:
        found["languages"] = languages
    return found or None


def drop_stray_closers(text: str) -> dict | None:
    """The object a text is once its surplus closing brackets are dropped: at most `max_stray_closers` times, only at the exact place where the parser stops with a missing comma and finds a closing
    bracket; None when that does not make the whole text parse. Nothing is added, no content moves; the pack's own checks (languages, clip keys, fields) follow."""
    for _ in range(ai_config.creative_pack.max_stray_closers + 1):
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError as error:
            if not error.msg.startswith("Expecting ',' delimiter") or error.pos >= len(text) or text[error.pos] not in "}]":
                return None
            text = text[:error.pos] + text[error.pos + 1:]
    return None


class CreativePack(Agent):
    name = "creative_pack"

    async def run(self, *, system: str, dossier: dict):
        """(the answer as a dict, the generation result with tokens and cost): ONE request; the provider's own bounded JSON repair applies, nothing else is asked."""
        data, result = await run_agent(self.name, self.gen, json.dumps(dossier, ensure_ascii=False), system=system)
        return (data if isinstance(data, dict) else {}), result


class CreativePolisher(Agent):
    name = "creative_polisher"

    async def run(self, *, system: str, request: dict):
        """The generation result of ONE request to the polishing model, which answers in text (`extract_json` reads it)."""
        return await run_agent(self.name, self.gen, json.dumps(request, ensure_ascii=False), system=system)


class CoverSubjectPicker(Agent):
    name = "cover_subject_picker"

    async def run(self, *, case: dict, people: list[dict]) -> list[dict]:
        data = await self.ask({"case": case, "people": people})
        return [p for p in (data.get("people") or []) if isinstance(p, dict) and p.get("entity_key")]
