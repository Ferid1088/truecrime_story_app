"""Agent base class for agents that deserve their own module and a typed `run`.

The agent's *definition* (model role, prompt file, output kind) is data in
config/agents.json; the class adds the job around it — building the input and
validating the answer — and is invoked only when a service needs it:

    verdict = await get_agent("case_title_critic").run(language="de", case=brief, titles=[...])

No agent contains prompt text or a model name. Agents take a generation
provider so tests can inject a fake."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar



class AgentError(RuntimeError):
    pass


class Agent(ABC):
    name: ClassVar[str]            # key in config/agents.json

    def __init__(self, gen=None):
        self._gen = gen

    @property
    def gen(self):
        if self._gen is None:
            from app.providers.generation import get_generation_provider

            self._gen = get_generation_provider()
        return self._gen

    @property
    def definition(self):
        from app.agents.runner import agent_def

        return agent_def(self.name)

    async def ask(self, payload: dict | str, *, images: list[str] | None = None,
                  **fmt: Any) -> dict:
        """One structured model call with this agent's definition."""
        from app.agents.runner import run_agent

        user = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        data, _ = await run_agent(self.name, self.gen, user, images=images, **fmt)
        return data if isinstance(data, dict) else {}

    @abstractmethod
    async def run(self, **inputs: Any) -> Any:
        """The agent's job; returns validated plain data."""
