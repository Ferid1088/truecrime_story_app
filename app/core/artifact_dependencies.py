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
    "short_moment_selection": "shared",
    "short_script_captions": "per_language",
    "short_render": "per_language",
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
    "chapter_cards": ["chapter_plan", "timeline_cards", "spoken_version"],
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
    "short_moment_selection": ["final_render", "master_story"],
    "short_script_captions": ["short_moment_selection", "final_render", "master_story"],
    "short_render": ["short_script_captions", "final_render"],
}

# Configuration is an input at the stage that reads it; it is not a reason to
# invalidate every artifact in the job.  Names are stable perturbation labels
# used by the soundness harness and intentionally do not expose provider
# secrets.  A direct config input is predicted at that artifact and is then
# followed through the ordinary artifact graph.
ARTIFACT_CONFIG_INPUTS: dict[str, set[str]] = {
    "master_story": {"documentary", "agent_model_routing"},
    "beats": {"documentary", "agent_model_routing"},
    "audio_blocks": {"audio"},
    "spoken_version": {"voice", "language_profile", "pronunciation_voice",
                        "agent_model_routing"},
    "voice_takes": {"voice", "pronunciation_voice", "agent_model_routing"},
    # The persisted language manifest is owned by audio_timing in the
    # snapshot observer and contains the final mix's music placement and
    # library identity.  Music-library edits therefore affect this saved
    # output through documentary/music.py, not through the provider mock.
    "audio_timing": {"audio", "voice", "music", "music_history_library"},
    "visual_needs": {"visual", "agent_model_routing"},
    "visual_asset": {"visual"},
    "visual_shots": {"visual", "agent_model_routing"},
    "visual_pieces": {"visual", "agent_model_routing"},
    "visual_plan": {"visual", "agent_model_routing"},
    "chapter_plan": {"chapters", "agent_model_routing"},
    "chapter_cards": {"chapters", "language_profile", "agent_model_routing"},
    "timeline_cards": {"chapters", "agent_model_routing"},
    # Intro rendering uses the output dimensions/fps from render config.
    "intro": {"intro_design", "logo_bytes", "render"},
    "music_cues": {"music", "music_history_library", "audio"},
    "captions": {"voice", "language_profile"},
    "production_script": {"documentary", "render", "chapters", "visual",
                          "audio", "host", "music", "voice", "naming",
                          "language_profile", "pronunciation_voice"},
    "final_render": {"render", "intro_design", "channel_identity", "logo_bytes"},
    "research_result": {"research"},
    "host_plan": {"host", "documentary", "agent_model_routing"},
    "host_segments": {"host", "language_profile", "channel_identity", "agent_model_routing"},
    "naming": {"documentary"},
    "film_length": {"audio", "voice"},
    "short_moment_selection": {"short_form", "documentary"},
    "short_script_captions": {"short_form", "language_profile", "agent_model_routing"},
    "short_render": {"short_form", "render", "language_profile"},
}


def artifact_closure(roots: set[str]) -> set[str]:
    """Return roots plus all downstream artifact types."""
    reached = set(roots)
    changed = True
    while changed:
        changed = False
        for target, deps in ARTIFACT_DEPENDENCIES.items():
            if target not in reached and set(deps) & reached:
                reached.add(target)
                changed = True
    return reached


def config_artifact_roots(config_sections: set[str]) -> set[str]:
    """Return direct artifact producers that read changed config sections."""
    return {artifact for artifact, inputs in ARTIFACT_CONFIG_INPUTS.items()
            if inputs & config_sections}


def predicted_for_config(config_sections: set[str], languages: tuple[str, ...] =
                         ("en", "de", "fa", "ar")) -> set[str]:
    """Predict scoped artifact keys for a typed config perturbation."""
    types = artifact_closure(config_artifact_roots(config_sections))
    predicted = {
        f"{artifact}:{language}"
        if ARTIFACT_SCOPES[artifact] == "per_language" else f"{artifact}:shared"
        for artifact in types for language in languages
    }
    # The music stage also registers the unconsumed library as an explicit
    # shared artifact.  This is a real saved output, not registry telemetry;
    # music-library/history edits therefore predict that shared key too.
    if config_sections & {"music", "music_history_library"}:
        predicted.add("music_cues:shared")
    return predicted


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
    if set(ARTIFACT_CONFIG_INPUTS) != set(ARTIFACT_SCOPES):
        raise ValueError("every artifact type must declare config_inputs")
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
