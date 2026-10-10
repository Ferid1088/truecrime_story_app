"""Run an agent by name: one place where a stage turns into a model call.

    data, res = await run_agent("story.consistency", gen, user_json, system=system)
    res       = await run_agent("story.write_act",   gen, user_json, system=system)

The agent definition (config/agents.json) says which model role it uses, which
prompt file it loads and whether it returns JSON or text. A stage passes the
input (and, where the prompt is assembled from live settings, the finished
`system` text); it never names a model. Everything that should apply to every
agent call (cost ledger, retries, tracing) hooks in here."""

from __future__ import annotations

from typing import Any

from app.agents.base import AgentError
from app.core.ai_config import ai_config
from app.core.prompts import prompt


def agent_def(name: str):
    try:
        return ai_config.agents[name]
    except KeyError:
        raise AgentError(f"unknown agent {name!r} (define it in config/agents.json)") from None


async def run_agent(name: str, gen, user: str, *, system: str | None = None,
                    role: str | None = None, images: list[str] | None = None,
                    **fmt: Any):
    """Structured agents return (data, result), text agents return the result —
    exactly what the generation provider returns."""
    d = agent_def(name)
    if system is None:
        if d.prompt is None:
            raise AgentError(f"agent {name!r} has no prompt file; the stage must pass `system`")
        text = prompt(d.prompt)
        system = text.format(**fmt) if fmt else text
    use_role = role or d.role
    extra = {"images": images} if images else {}
    if d.output == "text":
        return await gen.generate_text(use_role, system, user, **extra)
    return await gen.generate_structured(use_role, system, user, **extra)
