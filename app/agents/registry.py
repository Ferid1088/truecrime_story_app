"""The agent directory. Definitions live in config/agents.json (role, prompt
file, output kind); this module lists them and builds the agents that have a
class of their own, only when invoked.

    from app.agents.registry import get_agent, invoke, catalog
    await invoke("case_title_critic", language="de", ...)     # class agents
    await run_agent("story.consistency", gen, user, system=s) # any agent, by name
    catalog()        # every agent: name, role, model alias, prompt, class, used_by"""

from __future__ import annotations

import importlib

from app.agents.base import Agent, AgentError
from app.core.ai_config import ai_config

# agents with a class of their own: name -> (module, class)
CLASSES: dict[str, tuple[str, str]] = {
    "case_naming_agent": ("app.agents.naming", "CaseNamingAgent"),
    "case_title_critic": ("app.agents.naming", "CaseTitleCritic"),
    "native_title_critic": ("app.agents.naming", "NativeTitleCritic"),
    "thumbnail_critic": ("app.agents.thumbnail", "ThumbnailCritic"),
    "case_status_verifier": ("app.agents.status", "CaseStatusVerifier"),
}


def catalog() -> list[dict]:
    routing = ai_config.generation_provider().routing
    return [{"name": n, "role": d.role, "model_alias": routing.get(d.role), "output": d.output,
             "prompt": d.prompt, "class": ".".join(CLASSES[n]) if n in CLASSES else None,
             "used_by": d.used_by, "description": d.description}
            for n, d in sorted(ai_config.agents.items())]


def get_agent(name: str, **kwargs) -> Agent:
    if name not in ai_config.agents:
        raise AgentError(f"unknown agent {name!r}")
    if name not in CLASSES:
        raise AgentError(f"agent {name!r} has no class of its own; call "
                         f"run_agent({name!r}, gen, user, ...)")
    module, cls = CLASSES[name]
    return getattr(importlib.import_module(module), cls)(**kwargs)


async def invoke(name: str, /, **inputs):
    return await get_agent(name).run(**inputs)
