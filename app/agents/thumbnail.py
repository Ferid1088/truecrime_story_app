"""Thumbnail vision critic. Prompt: prompts/agents/thumbnail_critic.md."""

from __future__ import annotations

import base64
from pathlib import Path

from app.agents.base import Agent


class ThumbnailCritic(Agent):
    name = "thumbnail_critic"

    async def run(self, *, image_path: Path, language: str, episode_title: str,
                  status_label: str) -> dict | None:
        data = base64.b64encode(Path(image_path).read_bytes()).decode()
        out = await self.ask(
            {"language": language, "episode_title": episode_title, "status_label": status_label,
             "host": "Fereidoun, the channel's recurring host (same outfit as the video)"},
            images=[f"data:image/jpeg;base64,{data}"])
        return out or None
