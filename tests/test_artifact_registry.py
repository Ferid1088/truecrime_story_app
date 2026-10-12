import pytest

from app.core.artifact_dependencies import (
    ARTIFACT_DEPENDENCIES,
    ARTIFACT_SCOPES,
    direct_dependents,
    validate_dependency_table,
)
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
    assert set(ARTIFACT_DEPENDENCIES) == set(ARTIFACT_SCOPES)
    assert ARTIFACT_SCOPES["voice_takes"] == "per_language"
    assert ARTIFACT_SCOPES["beats"] == "shared"
    assert "audio_timing" in direct_dependents("voice_takes")


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


def _timing_graph(registry, language, blocks=None, master=None):
    if blocks is None:
        beats = registry.register("beats", {"scope": "shared"}, {"text": "beat"})
        master = registry.register("master_story", {"scope": "shared"}, {"text": "story"})
        blocks = registry.register("audio_blocks", {"scope": "shared"}, {"plan": 1}, [beats.id])
    spoken = registry.register("spoken_version", {"language": language}, {"text": language},
                               [master.id])
    takes = registry.register("voice_takes", {"language": language}, {"duration": 10},
                              [blocks.id, spoken.id])
    timing = registry.register("audio_timing", {"language": language}, {"duration": 10},
                               [takes.id])
    music = registry.register("music_cues", {"language": language}, {"cue": language},
                              [blocks.id, timing.id])
    captions = registry.register("captions", {"language": language}, {"words": language},
                                 [spoken.id, timing.id])
    script = registry.register("production_script", {"language": language}, {"script": language},
                               [spoken.id, takes.id, timing.id, music.id, captions.id])
    final = registry.register("final_render", {"language": language}, {"script": language},
                              [script.id, registry.register("intro", {"language": language},
                                                             {"intro": language}).id])
    return takes.id, {"audio_timing", "music_cues", "captions", "production_script", "final_render"}, final.id


def test_voice_duration_invalidates_only_that_language_timing_consumers(db_session):
    registry = ArtifactRegistry(db_session)
    en_take, expected, _ = _timing_graph(registry, "en")
    de_take, _, _ = _timing_graph(registry, "de")
    affected = registry.invalidate([en_take], "voice duration changed")
    assert set(affected) == {"voice_takes", *expected}
    assert de_take not in {item for values in affected.values() for item in values}


def test_shared_beat_invalidates_all_language_dependents(db_session):
    registry = ArtifactRegistry(db_session)
    beat = registry.register("beats", {"scope": "shared"}, {"text": "old"})
    master = registry.register("master_story", {"scope": "shared"}, {"text": "story"})
    blocks = registry.register("audio_blocks", {"scope": "shared"}, {"text": "old"}, [beat.id])
    en_take, _, _ = _timing_graph(registry, "en", blocks, master)
    de_take, _, _ = _timing_graph(registry, "de", blocks, master)
    affected = registry.invalidate([beat.id], "beat text changed")
    flat = {item for values in affected.values() for item in values}
    assert affected["beats"] == [beat.id]
    assert en_take in flat and de_take in flat


def test_asset_replacement_only_invalidates_shots_that_use_it(db_session):
    registry = ArtifactRegistry(db_session)
    asset_a = registry.register("visual_asset", {"asset": "a"}, {"sha": "a"})
    asset_b = registry.register("visual_asset", {"asset": "b"}, {"sha": "b"})
    shot_a = registry.register("visual_shots", {"shot": "a"}, {"asset": "a"}, [asset_a.id])
    shot_b = registry.register("visual_shots", {"shot": "b"}, {"asset": "b"}, [asset_b.id])
    affected = registry.invalidate([asset_a.id], "asset replaced")
    flat = {item for values in affected.values() for item in values}
    assert asset_a.id in flat and shot_a.id in flat
    assert asset_b.id not in flat and shot_b.id not in flat


def test_voice_timing_invalidation_is_language_local(db_session):
    registry = ArtifactRegistry(db_session)
    master = registry.register("master_story", {"case": 2}, {"text": "story"})
    beats = registry.register("beats", {"case": 2}, {"text": "beat"}, [master.id])
    blocks = registry.register("audio_blocks", {"case": 2}, {"blocks": 1}, [beats.id])
    spoken = {}
    final = {}
    for language in ("en", "de"):
        spoken[language] = registry.register("spoken_version", {"language": language},
                                             {"text": language}, [master.id])
        take = registry.register("voice_takes", {"language": language},
                                 {"duration": 10}, [blocks.id, spoken[language].id])
        timing = registry.register("audio_timing", {"language": language},
                                   {"duration": 10}, [take.id])
        music = registry.register("music_cues", {"language": language}, {"cue": language},
                                  [blocks.id, timing.id])
        captions = registry.register("captions", {"language": language},
                                     {"captions": language}, [spoken[language].id, timing.id])
        script = registry.register(
            "production_script", {"language": language}, {"script": language},
            [spoken[language].id, take.id, timing.id, captions.id, music.id])
        final[language] = registry.register("final_render", {"language": language},
                                             {"render": language}, [script.id])
    en_take = registry.register("voice_takes", {"language": "en"}, {"duration": 11},
                                [blocks.id, spoken["en"].id])
    affected = registry.invalidate([en_take.id], "duration changed")
    flat = {item for values in affected.values() for item in values}
    assert en_take.id in flat and final["en"].id in flat
    assert final["de"].id not in flat and spoken["de"].id not in flat
