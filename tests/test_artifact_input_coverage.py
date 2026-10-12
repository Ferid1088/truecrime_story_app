"""Guard the shadow fingerprint's explicit input coverage.

This is intentionally a hash-level perturbation matrix.  It does not make the
registry authoritative or skip pipeline work; it prevents a newly added typed
configuration source from silently being omitted from the shadow input.
"""
import copy

from app.core.ai_config import ai_config
from app.core.fingerprint import input_fingerprint
from app.db.models import DocumentaryJob
from app.services.artifact_shadow import _source_payload

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
