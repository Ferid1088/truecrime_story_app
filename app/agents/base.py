"""Agent base class: one agent = one role, one prompt file, one job.

An agent is defined in its own module under `app/agents/`, names its model
role (routed in config/models.json) and its prompt (prompts/agents/<name>.md),
and is invoked only when a service needs it:

    verdict = await get_agent("case_title_critic").run(language="de", case=brief, titles=[...])

No agent contains prompt text or a model name; a service never builds an
agent's prompt. Agents take a generation provider so tests can inject a fake."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.core.prompts import prompt


class AgentError(RuntimeError):
    pass


class Agent(ABC):
    name: ClassVar[str]            # registry key, also the prompt file name
    role: ClassVar[str]            # model role in config/models.json
    description: ClassVar[str] = ""
    prompt_name: ClassVar[str | None] = None   # default: agents/<name>

    def __init__(self, gen=None):
        self._gen = gen

    @property
    def gen(self):
        if self._gen is None:
            from app.providers.generation import get_generation_provider

            self._gen = get_generation_provider()
        return self._gen

    def system(self, **fmt: Any) -> str:
        text = prompt(self.prompt_name or f"agents/{self.name}")
        return text.format(**fmt) if fmt else text

    async def ask(self, payload: dict | str, *, images: list[str] | None = None,
                  **fmt: Any) -> dict:
        """One structured model call with this agent's prompt and role."""
        user = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        extra = {"images": images} if images else {}
        data, _ = await self.gen.generate_structured(self.role, self.system(**fmt), user, **extra)
        return data if isinstance(data, dict) else {}

    @abstractmethod
    async def run(self, **inputs: Any) -> Any:
        """The agent's job; returns validated plain data."""
