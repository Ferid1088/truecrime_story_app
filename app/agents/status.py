"""Case status verifier. Prompt: prompts/agents/case_status_verifier.md."""

from __future__ import annotations

from app.agents.base import Agent
from app.lifecycle.status import normalize_status


class CaseStatusVerifier(Agent):
    name = "case_status_verifier"

    async def run(self, *, case: dict, documents: list[dict]) -> dict:
        data = await self.ask({"case": case, "documents": documents[:24]})
        data["status"] = normalize_status(data.get("status"))
        try:
            data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0)))
        except (TypeError, ValueError):
            data["confidence"] = 0.0
        known = {d.get("url") for d in documents}
        data["supporting_urls"] = [u for u in data.get("supporting_urls") or [] if u in known]
        return data
