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
    "blueprint": ("app.documentary.blueprint", "NarrativeDirector"),
    "audio_plan": ("app.documentary.audio_director", "AudioDirector"),
    "spoken": ("app.documentary.spoken", "SpokenNarrator"),
    "chapters": ("app.documentary.chapters", "ChapterWriter"),
    "host": ("app.documentary.host", "HostDirector"),
    "voice_performance": ("app.documentary.voice_performance", "VoicePerformanceDirector"),
    "visual_planner": ("app.documentary.visuals.planner", "VisualPlanner"),
    "visual_research": ("app.documentary.visuals.research", "VisualResearchAgent"),
    "visual_verification": ("app.documentary.visuals.verification", "VisualVerificationAgent"),
    "visual_director": ("app.documentary.visuals.director", "VisualDirector"),
    "visual_auditor": ("app.documentary.visuals.auditor", "VisualAuditor"),
    "video_auditor": ("app.documentary.visuals.video_auditor", "VideoAuditor"),
    "footage": ("app.documentary.visuals.footage", "FootageAgent"),
    "critics": ("app.documentary.production.critics", "DocumentaryCritics"),
}


def stage_agent_class(name: str) -> type:
    if name not in STAGE_AGENTS:
        raise AgentError(f"unknown stage agent {name!r}; known: {', '.join(sorted(STAGE_AGENTS))}")
    module, cls = STAGE_AGENTS[name]
    return getattr(importlib.import_module(module), cls)


def get_stage_agent(name: str, **kwargs):
    """A new instance, created on the call (nothing is built until a stage needs it)."""
    return stage_agent_class(name)(**kwargs)
