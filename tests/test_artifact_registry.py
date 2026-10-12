import pytest

from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES, validate_dependency_table
from app.core.member_ids import assign_new_ids, ensure_document_member_ids, load_legacy_ids
from app.services.artifacts import ArtifactGraphError, ArtifactRegistry


def test_member_ids_survive_reorder_insert_and_delete():
    original = assign_new_ids("blueprint_1", "beat", [{"text": "a"}, {"text": "b"}])
    by_text = {row["text"]: row["id"] for row in original}
    changed = assign_new_ids("blueprint_1", "beat", [original[1], {"text": "c"}, original[0]])
    assert {row["text"]: row["id"] for row in changed}["a"] == by_text["a"]
    assert {row["text"]: row["id"] for row in changed}["b"] == by_text["b"]
    assert changed[1]["id"].startswith("beat_")


def test_legacy_ids_are_deterministic_and_flagged():
    one = load_legacy_ids("blueprint_1", "beat", [{"text": "a"}, {"text": "b"}])
    two = load_legacy_ids("blueprint_1", "beat", [{"text": "a"}, {"text": "b"}])
    assert [x["id"] for x in one] == [x["id"] for x in two]
    assert all(x["legacy_id"] for x in one)


def test_known_document_members_get_prefixed_ids():
    doc = ensure_document_member_ids("script_1", {"beats": [{}], "shots": [{}], "captions": [{}]})
    assert doc["beats"][0]["id"].startswith("beat_")
    assert doc["shots"][0]["id"].startswith("shot_")
    assert doc["captions"][0]["id"].startswith("caption_")


def test_dependency_table_is_acyclic_and_documented_types_are_present():
    validate_dependency_table()
    assert {"master_story", "beats", "audio_blocks", "voice_takes", "spoken_version",
            "visual_needs", "visual_shots", "visual_pieces", "visual_plan", "chapter_plan",
            "chapter_cards", "timeline_cards", "intro", "music_cues", "captions",
            "production_script", "final_render"} <= set(ARTIFACT_DEPENDENCIES)


def test_registry_fresh_version_scope_preview_and_invalidation(db_session):
    registry = ArtifactRegistry(db_session)
    master = registry.register("master_story", {"case": 1}, {"text": "a"})
    beat = registry.register("beats", {"case": 1}, {"text": "beat"}, [master.id])
    again = registry.register("beats", {"case": 1}, {"text": "beat"}, [master.id])
    assert again.id == beat.id and again.version == beat.version and again.status == "fresh"
    preview = registry.scope_preview([master.id, beat.id])
    assert preview["label"] == "2 of 2" and preview["counts"]["beats"] == 1
    affected = registry.invalidate([master.id], "beat changed")
    assert set(affected) == {"master_story", "beats"}
    assert registry.invalidate([master.id], "same reason") == affected


def test_registry_rejects_cycles_and_absolute_paths(db_session):
    registry = ArtifactRegistry(db_session)
    a = registry.register("master_story", {"a": 1}, {})
    b = registry.register("beats", {"b": 1}, {}, [a.id])
    with pytest.raises(ArtifactGraphError):
        registry.register("master_story", {"a": 1}, {}, [b.id])
    with pytest.raises(ArtifactGraphError):
        registry.register("intro", {"a": 2}, {}, output_ref="/absolute/file")
