"""Run an agent by name: one place where a stage turns into a model call.

    data, res = await run_agent("language.inspect.en", gen, user_json, system=system)
    res       = await run_agent("language.final.en", gen, user_json, system=system)

The agent definition (config/agents.json) says which model role it uses, which
prompt file it loads and whether it returns JSON or text. A stage passes the
input (and, where the prompt is assembled from live settings, the finished
`system` text); it never names a model. Everything that should apply to every
agent call (cost ledger, retries, tracing) hooks in here."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.base import AgentError
from app.core.ai_config import ai_config
from app.core.prompts import prompt

log = logging.getLogger(__name__)


def agent_def(name: str):
    try:
        return ai_config.agents[name]
    except KeyError:
        raise AgentError(f"unknown agent {name!r} (define it in config/agents.json)") from None


async def run_agent(name: str, gen, user: str, *, system: str | None = None,
                    role: str | None = None, images: list[str] | None = None,
                    prefix: str | None = None, cache_key: str | None = None,
                    output: str | None = None, **fmt: Any):
    """Structured agents return (data, result), text agents return the result —
    exactly what the generation provider returns. `prefix` is the stable text
    that repeated calls share (sent first, so the provider can cache it). `output` ("text" or "structured") overrides the agent's own output kind for one call:
    a stage that wants the plain answer of an agent that normally returns JSON (the host writer's fallback) says so here instead of calling the provider itself."""
    d = agent_def(name)
    if system is None:
        if d.prompt is None:
            raise AgentError(f"agent {name!r} has no prompt file; the stage must pass `system`")
        text = prompt(d.prompt)
        system = text.format(**fmt) if fmt else text
    from app.naming.standard import with_standard

    system = with_standard(name, system)
    use_role = role or d.role
    extra: dict[str, Any] = {"images": images} if images else {}
    if prefix or cache_key:
        extra.update({k: v for k, v in (("prefix", prefix), ("cache_key", cache_key)) if v})
    if (output or d.output) == "text":
        return await gen.generate_text(use_role, system, user, **extra)
    answer, result = await gen.generate_structured(use_role, system, user, **extra)
    if name in ai_config.second_look.agents and isinstance(answer, dict) and answer:
        return await _second_look(gen, use_role, system, user, answer, result, extra)
    return answer, result


async def _second_look(gen, use_role: str, system: str, user: str, answer: dict, result, extra: dict):
    """The same model, in the same request, reads its own first answer once more against the evidence and the rules and returns the final one (config second_look). A second answer that is
    missing or empty leaves the first; nothing here is a separate inspector."""
    from app.providers.generation.base import GenerationError

    again = json.dumps({"request": user, "your_first_answer": answer, "second_look": prompt("agents/second_look")}, ensure_ascii=False)
    try:
        final, second = await gen.generate_structured(use_role, system, again, **extra)
    except GenerationError as exc:
        log.warning("second look of %s failed, the first answer stands: %s", use_role, exc)
        return answer, result
    return (final, second) if isinstance(final, dict) and final else (answer, result)
