"""The declarative artifact graph and its scope semantics.

The graph is shadow-only today. ``audio_timing`` is deliberately separate
from the editorial audio plan: the former is the per-language timing manifest
produced after voice rendering, while the latter is the shared direction.
"""
from __future__ import annotations

from typing import Literal

ArtifactScope = Literal["shared", "per_language"]


# ``shared`` means that one artifact is valid for all language branches.
# ``per_language`` means its scope must include a language.
ARTIFACT_SCOPES: dict[str, ArtifactScope] = {
    "master_story": "shared",
    "beats": "shared",
    "audio_blocks": "shared",
    "spoken_version": "per_language",
    "voice_takes": "per_language",
    "audio_timing": "per_language",
    "visual_needs": "shared",
    "visual_asset": "shared",
    "visual_shots": "shared",
    "visual_pieces": "shared",
    "visual_plan": "shared",
    "chapter_plan": "shared",
    "chapter_cards": "per_language",
    "timeline_cards": "per_language",
    "intro": "per_language",
    "music_cues": "per_language",
    "captions": "per_language",
    "production_script": "per_language",
    "final_render": "per_language",
    "research_result": "shared",
    "host_plan": "shared",
    "host_segments": "per_language",
    "naming": "shared",
    "film_length": "per_language",
}


# A dependency is an input read by the producing stage, not merely a
# conceptual predecessor. Voice duration/timestamps are consumed directly by
# music cues, captions, and the production script. Visual shot timing and
# chapter/timeline card placement are computed inside production_script from
# the manifest; their shared plan/card rows do not read a language's timing.
# This keeps a Persian retake from invalidating German artifacts.
ARTIFACT_DEPENDENCIES: dict[str, list[str]] = {
    "master_story": ["research_result"],
    "beats": ["master_story"],
    "audio_blocks": ["beats"],
    "spoken_version": ["master_story"],
    "voice_takes": ["audio_blocks", "spoken_version"],
    "audio_timing": ["voice_takes"],
    "visual_needs": ["beats"],
    # Persisted verification reads case.canonical_title as case context.
    "visual_asset": ["naming"],
    "visual_shots": ["visual_needs", "visual_asset"],
    "visual_pieces": ["visual_asset", "visual_shots"],
    "visual_plan": ["visual_needs", "visual_shots", "visual_pieces"],
    "chapter_plan": ["master_story", "beats"],
    "chapter_cards": ["chapter_plan", "timeline_cards"],
    "timeline_cards": ["beats"],
    "intro": [],
    "music_cues": ["audio_blocks", "audio_timing"],
    "captions": ["spoken_version", "audio_timing"],
    "production_script": [
        "spoken_version", "voice_takes", "audio_timing", "visual_plan",
        "chapter_cards", "timeline_cards", "captions", "music_cues",
        "host_segments", "film_length", "naming",
    ],
    "final_render": ["production_script", "intro"],
    "research_result": [],
    "host_plan": ["beats"],
    "host_segments": ["host_plan", "spoken_version"],
    "naming": ["master_story"],
    "film_length": ["spoken_version", "audio_blocks"],
}


def direct_dependents(artifact_type: str) -> set[str]:
    """Return direct downstream types for documentation and tests."""
    return {name for name, deps in ARTIFACT_DEPENDENCIES.items()
            if artifact_type in deps}


def validate_dependency_table() -> None:
    missing = {dep for deps in ARTIFACT_DEPENDENCIES.values() for dep in deps} - set(ARTIFACT_DEPENDENCIES)
    if missing:
        raise ValueError(f"unknown artifact dependency types: {sorted(missing)}")
    if set(ARTIFACT_DEPENDENCIES) != set(ARTIFACT_SCOPES):
        raise ValueError("every artifact type must declare shared/per-language scope")
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError(f"artifact dependency cycle at {node}")
        if node in done:
            return
        visiting.add(node)
        for dep in ARTIFACT_DEPENDENCIES[node]:
            visit(dep)
        visiting.remove(node)
        done.add(node)

    for artifact in ARTIFACT_DEPENDENCIES:
        visit(artifact)


validate_dependency_table()
