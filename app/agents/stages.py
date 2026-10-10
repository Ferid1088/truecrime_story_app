"""The film-making stage agents, listed in one place and built only when needed.

Each is a class with its own typed entry points (`create`, `run`, ...) that lives
next to the code it works on; this directory is how the app finds them, so a
stage asks for an agent by name instead of importing every module up front:

    from app.agents.stages import get_stage_agent
    row = await get_stage_agent("blueprint").create(db, case, master)

Their prompts are in prompts/, their model roles in config/agents.json."""

from __future__ import annotations

import importlib

from app.agents.base import AgentError

# stage name -> (module, class)
STAGE_AGENTS: dict[str, tuple[str, str]] = {
    "blueprint": ("app.agents.blueprint", "NarrativeDirector"),
    "audio_plan": ("app.agents.audio_plan", "AudioDirector"),
    "spoken": ("app.agents.spoken", "SpokenNarrator"),
    "chapters": ("app.agents.chapters", "ChapterWriter"),
    "host": ("app.agents.host", "HostDirector"),
    "voice_performance": ("app.agents.voice_performance", "VoicePerformanceDirector"),
    "visual_planner": ("app.agents.visual_planner", "VisualPlanner"),
    "visual_research": ("app.agents.visual_research", "VisualResearchAgent"),
    "visual_verification": ("app.agents.visual_verification", "VisualVerificationAgent"),
    "visual_director": ("app.agents.visual_director", "VisualDirector"),
    "visual_auditor": ("app.agents.visual_auditor", "VisualAuditor"),
    "video_auditor": ("app.agents.video_auditor", "VideoAuditor"),
    "footage": ("app.agents.footage", "FootageAgent"),
    "critics": ("app.agents.critics", "DocumentaryCritics"),
}


def stage_agent_class(name: str) -> type:
    if name not in STAGE_AGENTS:
        raise AgentError(f"unknown stage agent {name!r}; known: {', '.join(sorted(STAGE_AGENTS))}")
    module, cls = STAGE_AGENTS[name]
    return getattr(importlib.import_module(module), cls)


def get_stage_agent(name: str, **kwargs):
    """A new instance, created on the call (nothing is built until a stage needs it)."""
    return stage_agent_class(name)(**kwargs)
