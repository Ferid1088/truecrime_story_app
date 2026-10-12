import json

from app.core.ai_config import ai_config
from app.core.artifact_dependencies import ARTIFACT_DEPENDENCIES
from app.db.models import Artifact, ArtifactDep, DocumentaryJob
from app.documentary.pipeline.base import StageRunner
from app.services.artifacts import ArtifactRegistry


def _register_fixture(registry, job_id, case_id, language=None):
    made = {}
    order = [
        "master_story", "beats", "audio_blocks", "spoken_version", "voice_takes",
        "visual_needs", "visual_shots", "visual_pieces", "visual_plan", "chapter_plan",
        "timeline_cards", "chapter_cards", "music_cues", "captions", "production_script",
        "intro", "final_render",
    ]
    for artifact_type in order:
        scope = {"job": job_id, "language": language, "type": artifact_type}
        deps = [made[dep] for dep in ARTIFACT_DEPENDENCIES[artifact_type] if dep in made]
        row = registry.register(artifact_type, scope, {"text": artifact_type, "language": language}, deps,
                                output_ref=f"row:{artifact_type}", case_id=case_id,
                                language=language, stage_job_id=job_id)
        made[artifact_type] = row.id
    return made


def test_shadow_graph_fixture_has_hashes_edges_four_languages_and_stable_rerun(db_session, monkeypatch):
    monkeypatch.setattr(ai_config.artifacts, "shadow_mode", True)
    job = DocumentaryJob(case_id=1, languages_json=json.dumps(["en", "de", "fa", "ar"]))
    db_session.add(job)
    db_session.commit()
    registry = ArtifactRegistry(db_session)
    _register_fixture(registry, job.id, job.case_id)
    for language in ("en", "de", "fa", "ar"):
        _register_fixture(registry, job.id, job.case_id, language)

    rows = db_session.query(Artifact).filter(Artifact.stage_job_id == job.id).all()
    assert rows and all(row.input_hash and row.type and json.loads(row.scope_json) for row in rows)
    ids = {row.id for row in rows}
    edges = db_session.query(ArtifactDep).filter(
        ArtifactDep.artifact_id.in_(ids), ArtifactDep.depends_on_id.in_(ids)
    ).all()
    assert all(edge.artifact_id in ids and edge.depends_on_id in ids for edge in edges)
    per_language = {row.language for row in rows if row.language}
    assert per_language == {"en", "de", "fa", "ar"}

    before = {row.id: (row.input_hash, row.version) for row in rows}
    _register_fixture(registry, job.id, job.case_id)
    after = {row.id: (row.input_hash, row.version) for row in rows}
    assert before == after


def test_one_beat_change_invalidates_only_declared_downstream_set(db_session):
    registry = ArtifactRegistry(db_session)
    made = {}
    for artifact_type in ("master_story", "beats", "audio_blocks", "spoken_version", "voice_takes",
                           "visual_needs", "visual_shots", "visual_pieces", "visual_plan",
                           "chapter_plan", "timeline_cards", "chapter_cards", "music_cues",
                           "captions", "production_script", "intro", "final_render"):
        deps = [made[x] for x in ARTIFACT_DEPENDENCIES[artifact_type] if x in made]
        row = registry.register(artifact_type, {"fixture": True, "type": artifact_type},
                                {"beat_text": "original", "type": artifact_type}, deps)
        made[artifact_type] = row.id
    affected = registry.invalidate([made["beats"]], "one beat text changed")
    expected = {"beats", "audio_blocks", "voice_takes", "visual_needs",
                "visual_shots", "visual_pieces", "visual_plan", "chapter_plan", "chapter_cards",
                "timeline_cards", "music_cues", "captions", "production_script", "final_render"}
    assert set(affected) == expected
    assert "master_story" not in affected and "intro" not in affected


def test_shadow_mode_does_not_change_stage_result(db_session, monkeypatch):
    outputs = []
    for enabled in (False, True):
        monkeypatch.setattr(ai_config.artifacts, "shadow_mode", enabled)
        job = DocumentaryJob(case_id=1, languages_json='["en"]', stages_json="[]")
        db_session.add(job)
        db_session.commit()
        runner = StageRunner(db_session, job)

        async def produce():
            return {"production_script": {"shots": [{"beat_id": "beat_fixed", "start": 0.0}]}}

        import asyncio
        asyncio.run(runner._stage("production:en", produce))
        outputs.append(json.loads(job.result_json))
    assert outputs[0] == outputs[1]
