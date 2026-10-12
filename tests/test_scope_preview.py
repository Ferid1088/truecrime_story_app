import json

from app.db.models import Artifact, Case, DocumentaryJob, PolicyEvent
from app.services.artifacts import ArtifactRegistry


def test_scope_preview_is_read_only_and_classifies_rebuilds(db_session, client, monkeypatch):
    suffix = str(id(db_session))
    case = Case(canonical_title="Preview case", slug=f"preview-{suffix}", language="en")
    db_session.add(case)
    db_session.flush()
    job = DocumentaryJob(case_id=case.id, languages_json='["en"]')
    db_session.add(job)
    db_session.flush()
    root = Artifact(id=f"preview-root-{suffix}", case_id=case.id, stage_job_id=job.id,
                    type="master_story", scope_json=json.dumps({"stage": "master_story"}),
                    input_hash="root", output_hash="root", status="fresh")
    child = Artifact(id=f"preview-child-{suffix}", case_id=case.id, stage_job_id=job.id,
                     type="beats", scope_json=json.dumps({"stage": "blueprint"}),
                     input_hash="child", output_hash="child", status="fresh")
    db_session.add_all([root, child])
    db_session.commit()
    before = db_session.query(PolicyEvent).count()
    monkeypatch.setattr("app.services.skip_policy.SkipPolicy.reusable_reasons", lambda *args: [])
    preview = ArtifactRegistry(db_session).scope_preview(
        [root.id, child.id], changed_artifact_ids=[root.id])
    after = db_session.query(PolicyEvent).count()
    assert before == after
    assert preview["estimated_rebuild"]["total"] == 1
    root_item = next(item for item in preview["artifacts"] if item["artifact_id"] == root.id)
    assert root_item["will_rerun"] is True
    assert root_item["unproven_will_rerun"] is False
    response = client.post(
        f"/api/documentary/{case.id}/artifacts/scope-preview",
        json={"artifact_ids": [root.id, child.id], "changed_artifact_ids": [root.id]},
    )
    assert response.status_code == 200
    print("SCOPE_PREVIEW_RESPONSE", json.dumps(response.json(), sort_keys=True))


def test_scope_preview_real_graph_closures_are_language_scoped_and_read_only(db_session, monkeypatch):
    from sqlalchemy import func, select

    from app.db.base import Base

    case = Case(canonical_title="Graph preview", slug="graph-preview", language="en")
    db_session.add(case)
    db_session.flush()
    job = DocumentaryJob(case_id=case.id, languages_json='["en", "de"]')
    db_session.add(job)
    db_session.flush()
    registry = ArtifactRegistry(db_session)
    made = {}

    def add(kind, language=None, deps=()):
        row = registry.register(
            kind, {"job": job.id, "type": kind, "language": language},
            {"fixture": kind, "language": language}, deps,
            case_id=case.id, language=language, stage_job_id=job.id,
            output_hash="saved",
        )
        made[(kind, language)] = row
        return row

    master = add("master_story")
    beats = add("beats", deps=[master.id])
    blocks = add("audio_blocks", deps=[beats.id])
    for language in ("en", "de"):
        spoken = add("spoken_version", language, [master.id])
        takes = add("voice_takes", language, [blocks.id, spoken.id])
        timing = add("audio_timing", language, [takes.id])
        music = add("music_cues", language, [blocks.id, timing.id])
        captions = add("captions", language, [spoken.id, timing.id])
        intro = add("intro", language)
        script = add("production_script", language,
                     [spoken.id, takes.id, timing.id, music.id, captions.id])
        add("final_render", language, [script.id, intro.id])

    monkeypatch.setattr("app.services.skip_policy.SkipPolicy.reusable_reasons", lambda *args: [])
    def counts():
        return {
            table.name: db_session.execute(select(func.count()).select_from(table)).scalar_one()
            for table in Base.metadata.sorted_tables
        }

    before = counts()
    master_preview = registry.scope_preview([master.id], [master.id])
    master_rerun = {item["artifact_id"] for item in master_preview["artifacts"]
                    if item["will_rerun"]}
    assert master_rerun == {
        row.id for (kind, _language), row in made.items() if kind != "intro"
    }

    spoken_de = made[("spoken_version", "de")]
    de_preview = registry.scope_preview([spoken_de.id], [spoken_de.id])
    de_rerun = {item["artifact_id"] for item in de_preview["artifacts"]
                if item["will_rerun"]}
    assert de_rerun == {
        row.id for (kind, language), row in made.items()
        if language in (None, "de") and kind in {
            "spoken_version", "voice_takes", "audio_timing", "music_cues",
            "captions", "production_script", "final_render",
        }
    }

    logo_roots = [made[("intro", language)].id for language in ("en", "de")]
    logo_preview = registry.scope_preview(logo_roots, logo_roots)
    logo_rerun = {item["artifact_id"] for item in logo_preview["artifacts"]
                  if item["will_rerun"]}
    assert logo_rerun == {
        row.id for (kind, language), row in made.items()
        if kind in {"intro", "final_render"}
    }
    assert counts() == before
