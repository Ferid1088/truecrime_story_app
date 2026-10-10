"""The agent directory: every agent by name, built only when invoked.

    from app.agents.registry import get_agent, invoke, catalog
    await invoke("case_title_critic", language="de", ...)
    catalog()    # name, role, prompt, module, description — nothing is imported

Agents of the older pipelines (story, localization, blueprint ...) are listed
with status "legacy": they still live next to their stage and are moved here
one at a time (each move = a prompt file + a class with `run`)."""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from app.agents.base import Agent, AgentError


@dataclass(frozen=True)
class AgentSpec:
    name: str
    module: str
    cls: str
    role: str
    description: str
    status: str = "ready"        # ready | legacy


_SPECS: dict[str, AgentSpec] = {}


def register(name: str, module: str, cls: str, role: str, description: str,
             status: str = "ready") -> None:
    _SPECS[name] = AgentSpec(name, module, cls, role, description, status)


register("case_naming_agent", "app.agents.naming", "CaseNamingAgent", "case_naming_agent",
         "Proposes native episode-title candidates for one language.")
register("case_title_critic", "app.agents.naming", "CaseTitleCritic", "case_title_critic",
         "Scores titles: specificity, memorability, spoiler and epistemic risk.")
register("native_title_critic", "app.agents.naming", "NativeTitleCritic", "native_title_critic",
         "Judges whether titles read as originally written in the language.")
register("thumbnail_critic", "app.agents.thumbnail", "ThumbnailCritic", "thumbnail_critic",
         "Looks at a composed thumbnail: professional or AI collage?")
register("case_status_verifier", "app.agents.status", "CaseStatusVerifier",
         "case_status_verifier", "Decides solved/unsolved from provided documents only.")
for _n, _m, _c, _r, _d in [
    ("story_director", "app.agents.story", "StoryDirector", "story_director", "Plans acts and opening."),
    ("writer", "app.agents.story", "WriterAgent", "writer", "Writes the story act by act."),
    ("engagement_critic", "app.agents.story", "EngagementCritic", "engagement_critic", "Scores engagement."),
    ("localization_writer", "app.agents.localization", "LocalizationPipeline", "localization_writer", "Localizes a master."),
    ("transcript_intelligence", "app.agents.transcript_intel", "TranscriptIntelligenceAgent", "transcript_intelligence_extractor", "Claims from transcripts."),
    ("narrative_director", "app.documentary.blueprint", "NarrativeDirector", "narrative_director", "Editorial blueprint."),
    ("audio_director", "app.documentary.audio_director", "AudioDirector", "audio_director", "Music, silence, breaths."),
    ("spoken_narrator", "app.documentary.spoken", "SpokenNarrator", "spoken_writer", "Spoken version per language."),
    ("host_director", "app.documentary.host", "HostDirector", "host_director", "Host plan and segments."),
    ("chapter_writer", "app.documentary.chapters", "ChapterWriter", "chapter_writer", "Chapter and title-card texts."),
    ("visual_planner", "app.documentary.visuals.planner", "VisualPlanner", "visual_planner", "What each beat needs to show."),
    ("visual_director", "app.documentary.visuals.director", "VisualDirector", "visual_director", "Shots per sentence."),
    ("visual_verifier", "app.documentary.visuals.verification", "VisualVerificationAgent", "visual_verifier", "Does the picture show what is claimed?"),
    ("visual_auditor", "app.documentary.visuals.auditor", "VisualAuditor", "visual_auditor", "May this picture be shown with these words?"),
    ("video_auditor", "app.documentary.visuals.video_auditor", "VideoAuditor", "video_auditor", "Judges video pieces frame by frame."),
    ("video_segmenter", "app.documentary.visuals.video_segmenter", "VideoSegmenter", "video_segmenter", "Cuts a video by meaning."),
    ("voice_performance_director", "app.documentary.voice_performance", "VoicePerformanceDirector", "voice_performance_director", "Audio tags and tension arc."),
    ("documentary_critics", "app.documentary.production.critics", "DocumentaryCritics", "production_critic", "Critic loop on the production script."),
]:
    register(_n, _m, _c, _r, _d, status="legacy")


def catalog() -> list[dict]:
    from app.core.ai_config import ai_config

    routing = ai_config.generation_provider().routing
    return [{"name": s.name, "role": s.role, "model_alias": routing.get(s.role),
             "module": s.module, "class": s.cls, "status": s.status,
             "prompt": f"agents/{s.name}" if s.status == "ready" else None,
             "description": s.description} for s in sorted(_SPECS.values(), key=lambda x: x.name)]


def get_agent(name: str, **kwargs) -> Agent:
    spec = _SPECS.get(name)
    if spec is None:
        raise AgentError(f"unknown agent {name!r}")
    if spec.status != "ready":
        raise AgentError(f"agent {name!r} is a legacy stage class ({spec.module}.{spec.cls}); "
                         "it has not been moved to the agent layer yet")
    cls = getattr(importlib.import_module(spec.module), spec.cls)
    return cls(**kwargs)


async def invoke(name: str, /, **inputs):
    return await get_agent(name).run(**inputs)
