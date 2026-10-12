"""The single declarative dependency table for artifact invalidation."""

ARTIFACT_DEPENDENCIES: dict[str, list[str]] = {
    "master_story": [],
    "beats": ["master_story"],
    "audio_blocks": ["beats"],
    "voice_takes": ["audio_blocks", "spoken_version"],
    "spoken_version": ["master_story"],
    "visual_needs": ["beats"],
    "visual_shots": ["visual_needs"],
    "visual_pieces": ["visual_shots"],
    "visual_plan": ["visual_needs", "visual_shots", "visual_pieces"],
    "chapter_plan": ["master_story", "beats"],
    "chapter_cards": ["chapter_plan", "timeline_cards"],
    "timeline_cards": ["beats"],
    "intro": [],
    "music_cues": ["audio_blocks"],
    "captions": ["spoken_version", "voice_takes"],
    "production_script": ["spoken_version", "voice_takes", "visual_plan", "chapter_cards", "captions", "music_cues"],
    "final_render": ["production_script", "intro"],
}


def validate_dependency_table() -> None:
    missing = {dep for deps in ARTIFACT_DEPENDENCIES.values() for dep in deps} - set(ARTIFACT_DEPENDENCIES)
    if missing:
        raise ValueError(f"unknown artifact dependency types: {sorted(missing)}")
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
