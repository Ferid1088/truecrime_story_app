"""Seed the disposable database used by the real stage-control Playwright test."""

from __future__ import annotations

import json

from sqlalchemy import inspect, text

from app.db.base import SessionLocal
from app.db.models import (
    Artifact,
    ArtifactDep,
    Case,
    ChapterPlan,
    DocumentaryJob,
    EditorialBlueprint,
    StoryVersion,
)


def main() -> None:
    with SessionLocal() as db:
        bind = db.get_bind()
        if bind.dialect.name != "postgresql" or bind.url.database != "truecrime_e2e":
            raise RuntimeError("stage-control E2E seed is restricted to PostgreSQL database truecrime_e2e")
        tables = [name for name in inspect(bind).get_table_names() if name != "alembic_version"]
        if tables:
            quoted = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}"' for name in tables)
            db.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
            db.commit()
        db.query(ArtifactDep).delete()
        db.query(Artifact).delete()
        db.query(DocumentaryJob).delete()
        db.query(ChapterPlan).delete()
        db.query(EditorialBlueprint).delete()
        db.query(StoryVersion).delete()
        db.query(Case).delete()
        case = Case(canonical_title="Stage control E2E case", slug="stage-control-e2e",
                    language="en", status="story_ready", summary="Deterministic E2E case")
        db.add(case)
        db.flush()
        versions: dict[str, StoryVersion] = {}
        master = StoryVersion(case_id=case.id, version=1, kind="final", language="en",
                              narrative_angle="angle", story_text="A stable master story.",
                              narrative_structure=json.dumps({"sections": [{
                                  "title": "Arrival", "text": "A stable master chapter."
                              }]}),
                              status="approved", text_hash="master-hash")
        db.add(master)
        db.flush()
        for language in ("en", "de", "fa", "ar"):
            titles = {"en": "Arrival", "de": "Ankunft", "fa": "ورود", "ar": "الوصول"}
            version = StoryVersion(case_id=case.id, version=1, kind="localized", language=language,
                                   master_version_id=master.id, narrative_angle="angle",
                                   story_text=f"Original {language} chapter",
                                   narrative_structure=json.dumps({"sections": [{
                                       "title": titles[language],
                                       "text": f"Original {language} chapter",
                                   }]}),
                                   status="approved", text_hash=f"story-{language}")
            db.add(version)
            versions[language] = version
        db.flush()
        stages = [
            {"name": "research", "status": "done", "detail": {"summary": "Research saved"}},
            {"name": "master_story", "status": "done", "detail": {"summary": "Master story saved"}},
            *[{"name": f"spoken:{language}", "status": "done",
               "detail": {"version_id": versions[language].id}}
              for language in ("en", "de", "fa", "ar")],
            *[{"name": f"voice:{language}", "status": "done", "detail": {}}
              for language in ("en", "de", "fa", "ar")],
        ]
        job = DocumentaryJob(case_id=case.id, master_version_id=master.id,
                             mode="full", languages_json='["en","de","fa","ar"]',
                             render_profile="preview", status="completed", progress=1.0,
                             stages_json=json.dumps(stages), result_json="{}")
        db.add(job)
        db.flush()
        blueprint = EditorialBlueprint(
            case_id=case.id,
            story_version_id=master.id,
            status="valid",
            blueprint_json=json.dumps({"acts": [{"id": "act-1", "title": "Arrival"}]}),
        )
        db.add(blueprint)
        db.flush()
        db.add(ChapterPlan(
            case_id=case.id,
            blueprint_id=blueprint.id,
            version=1,
            status="approved",
            languages_json=json.dumps(["en", "de", "fa", "ar"]),
            plan_json=json.dumps({
                "languages": ["en", "de", "fa", "ar"],
                "film_title": {language: "Stage control" for language in ("en", "de", "fa", "ar")},
                "chapters": [{
                    "act_id": "act-1",
                    "number": 1,
                    "first_beat": "b1",
                    "last_beat": "b1",
                    "title": {"en": "Arrival", "de": "Ankunft", "fa": "ورود", "ar": "الوصول"},
                }],
                "events": [],
            }, ensure_ascii=False),
            audit_json="{}",
        ))
        db.flush()
        db.add(Artifact(id=f"research-{case.id}", case_id=case.id, stage_job_id=job.id,
                        type="research_result", language=None,
                        scope_json=json.dumps({"stage": "research", "language": None}),
                        input_hash="input-research", output_hash="output-research-v1",
                        status="fresh", version=1))
        roots: dict[str, Artifact] = {}
        for language in ("en", "de", "fa", "ar"):
            root = Artifact(id=f"spoken-{language}-{case.id}", case_id=case.id,
                            stage_job_id=job.id, type="spoken_version", language=language,
                            scope_json=json.dumps({"stage": "spoken", "language": language}),
                            input_hash=f"input-spoken-{language}", output_hash=f"output-spoken-{language}",
                            status="fresh")
            db.add(root)
            roots[language] = root
        for language in ("en", "de", "fa", "ar"):
            voice = Artifact(id=f"voice-{language}-{case.id}", case_id=case.id,
                             stage_job_id=job.id, type="voice_takes", language=language,
                             scope_json=json.dumps({"stage": "voice", "language": language}),
                             input_hash=f"input-voice-{language}", output_hash=f"output-voice-{language}",
                             status="fresh")
            db.add(voice)
            db.flush()
            db.add(ArtifactDep(artifact_id=voice.id, depends_on_id=roots[language].id))
        db.commit()
        print(json.dumps({"case_id": case.id, "job_id": job.id,
                          "languages": ["en", "de", "fa", "ar"]}))


if __name__ == "__main__":
    main()
