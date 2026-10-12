"""Guard the shadow fingerprint's explicit input coverage.

This is intentionally a hash-level perturbation matrix.  It does not make the
registry authoritative or skip pipeline work; it prevents a newly added typed
configuration source from silently being omitted from the shadow input.
"""
import copy
import json

from app.core.ai_config import ai_config
from app.core.fingerprint import input_fingerprint
from app.db.models import DocumentaryJob
from app.services.artifact_shadow import _source_payload
from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES

SOURCES = {
    "typed_config": "all registered artifact types",
    "language_list": "all registered artifact types",
    "pronunciation_and_voice": "spoken_version, voice_takes, audio_timing, captions",
    "channel_identity_and_logo": "intro, final_render",
    "intro_design": "intro, final_render",
    "music_history_and_library": "music_cues, production_script, final_render",
    "agents_and_model_routing": "all AI-produced artifact types",
}


def test_coverage_matrix_sources_are_explicit_and_perturbable(db_session):
    job = DocumentaryJob(case_id=1, languages_json='["en", "de", "fa", "ar"]')
    base = _source_payload(db_session, job, "render", "fa", {
        "music_history_snapshot": [{"position": 0, "track_id": 1}],
        "music_library_identity": [{"track_id": 1, "file_sha256": "a"}],
    }, "music_cues")
    assert set(base["config"]) == set(ai_config.model_fields)
    assert base["job_languages"] == ["en", "de", "fa", "ar"]
    assert set(SOURCES) == {
        "typed_config", "language_list", "pronunciation_and_voice",
        "channel_identity_and_logo", "intro_design", "music_history_and_library",
        "agents_and_model_routing",
    }

    changed = copy.deepcopy(base)
    changed["config"]["render"]["fps"] += 1
    assert input_fingerprint("music_cues", base, "artifact-input-v1") != \
        input_fingerprint("music_cues", changed, "artifact-input-v1")

    history_changed = copy.deepcopy(base)
    history_changed["music_history_snapshot"][0]["track_id"] = 2
    assert input_fingerprint("music_cues", base, "artifact-input-v1") != \
        input_fingerprint("music_cues", history_changed, "artifact-input-v1")


def test_every_typed_config_section_is_an_explicit_shadow_input(db_session):
    """A new typed section cannot silently disappear from the shadow hash."""
    job = DocumentaryJob(case_id=1, languages_json='["en", "de", "fa", "ar"]')
    base = _source_payload(db_session, job, "render", "en", {}, "final_render")
    artifact_types = tuple(ARTIFACT_DEPENDENCIES)
    for section in ai_config.model_fields:
        changed = copy.deepcopy(base)
        value = changed["config"][section]
        if isinstance(value, bool):
            changed["config"][section] = not value
        elif isinstance(value, (int, float)):
            changed["config"][section] = value + 1
        elif isinstance(value, str):
            changed["config"][section] = value + "__coverage__"
        elif isinstance(value, list):
            changed["config"][section] = [*value, "__coverage__"]
        else:
            changed["config"][section] = {**value, "__coverage__": True}
        for artifact_type in artifact_types:
            assert input_fingerprint(artifact_type, base, "artifact-input-v1") != \
                input_fingerprint(artifact_type, changed, "artifact-input-v1"), section


def test_required_perturbation_sources_change_their_shadow_input():
    base = {
        "job_languages": ["en", "de", "fa", "ar"],
        "config": {"pronunciation": {"enabled": True}, "voice": {"speed": 1.0}},
        "channel_identity": {"en": {"logo_sha256": "old"}},
        "intro_design": {"version": 1},
        "music_history_snapshot": [{"position": 0, "track_id": "a"}],
        "music_library_identity": [{"track_id": "a", "file_sha256": "old"}],
        "detail": {"source": "base"},
    }
    perturbations = {
        "language list": ("job_languages", ["en", "de", "fa"]),
        "pronunciation/voice": ("config", {"pronunciation": {"enabled": False},
                                              "voice": {"speed": 1.0}}),
        "channel identity/logo": ("channel_identity", {"en": {"logo_sha256": "new"}}),
        "intro design version": ("intro_design", {"version": 2}),
        "music history/library": ("music_history_snapshot", [{"position": 0, "track_id": "b"}]),
    }
    for name, (key, value) in perturbations.items():
        changed = copy.deepcopy(base)
        changed[key] = value
        assert input_fingerprint("final_render", base, "artifact-input-v1") != \
            input_fingerprint("final_render", changed, "artifact-input-v1"), name
    assert json.dumps(base, sort_keys=True, ensure_ascii=False)
