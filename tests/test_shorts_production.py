"""Offline production-worker proofs for Prompt 10G's isolation contract."""

import json
from pathlib import Path

from app.db.models import Case, DocumentaryJob, EditorialBlueprint, StoryVersion, Video
from app.documentary.jobs import plan_stages
from app.shortform import pipeline


def _job(db, tmp_path, status="completed"):
    suffix = db.query(Case).count() + 1
    case = Case(canonical_title=f"Shorts proof {suffix}", slug=f"shorts-proof-{suffix}", language="en")
    db.add(case)
    db.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="master", narrative_angle="evidence",
                         language="en", story_text="A finished evidence-led story.", status="approved")
    db.add(story)
    db.flush()
    job = DocumentaryJob(case_id=case.id, master_version_id=story.id, languages_json='["en", "de"]',
                         status=status, stages_json=json.dumps(plan_stages(["en", "de"])))
    db.add(job)
    db.flush()
    db.add(EditorialBlueprint(
        case_id=case.id, story_version_id=story.id, status="valid",
        blueprint_json=json.dumps({"beats": [
            {"id": "B1", "text": "The log fixes the last known time", "start_seconds": 3, "end_seconds": 30},
            {"id": "B2", "text": "A second record contradicts the first", "start_seconds": 31, "end_seconds": 58},
        ]}),
    ))
    for language in ("en", "de"):
        db.add(Video(case_id=case.id, job_id=job.id, language=language, state="rendered"))
    db.commit()
    return job


def _fake_export(output_dir: Path, **kwargs):
    for name in ("short.mp4", "cover.png", "subtitles.srt", "manifest.json"):
        (output_dir / name).write_bytes(name.encode())
    return {"video": "short.mp4", "cover": "cover.png", "subtitles": "subtitles.srt"}


def test_finished_film_starts_isolated_language_shorts(db_session, tmp_path, monkeypatch):
    job = _job(db_session, tmp_path)
    monkeypatch.setattr(pipeline.ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(pipeline.storage, "rel", lambda path: str(Path(path).resolve().relative_to(tmp_path)))
    monkeypatch.setattr(pipeline, "build_export_package", _fake_export)

    result = pipeline.produce_for_job(db_session, job)

    assert result["status"] == "completed"
    assert result["renders"] == len(db_session.query(pipeline.RenderedShort).all())
    assert {row.language for row in db_session.query(pipeline.RenderedShort).all()} == {"en", "de"}
    assert all((tmp_path / row.video_path).exists() for row in db_session.query(pipeline.RenderedShort).all())
    stages = {row["name"]: row for row in json.loads(job.stages_json)}
    assert stages["short_selection"]["status"] == "done"
    assert stages["short_render:en"]["status"] == "done"
    assert stages["short_render:de"]["status"] == "done"


def test_short_failure_does_not_change_long_film_state(db_session, tmp_path, monkeypatch):
    job = _job(db_session, tmp_path)
    film = db_session.query(Video).filter_by(job_id=job.id, language="en").one()
    film.file_path = "long-film.mp4"
    db_session.commit()
    original_status = job.status

    def fail_de(output_dir: Path, **kwargs):
        if kwargs["language"] == "de":
            raise RuntimeError("injected short failure")
        return _fake_export(output_dir, **kwargs)

    monkeypatch.setattr(pipeline.ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(pipeline.storage, "rel", lambda path: str(Path(path).resolve().relative_to(tmp_path)))
    monkeypatch.setattr(pipeline, "build_export_package", fail_de)
    result = pipeline.produce_for_job(db_session, job)

    db_session.refresh(job)
    assert job.status == original_status == "completed"
    assert result["errors"]
    assert db_session.query(pipeline.RenderedShort).filter_by(language="en", status="done").count() >= 1
    assert db_session.query(pipeline.RenderedShort).filter_by(language="de", status="failed").count() >= 1
    assert film.file_path == "long-film.mp4"


def test_shorts_do_not_start_before_finished_or_after_failed(db_session, tmp_path):
    for status in ("running", "failed"):
        job = _job(db_session, tmp_path, status=status)
        assert pipeline.produce_for_job(db_session, job)["status"] == "skipped"
        assert db_session.query(pipeline.ShortCandidate).filter_by(film_job_id=job.id).count() == 0
