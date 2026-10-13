"""Backend stage-control E2E executed against the disposable E2E database."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

from mock_payload import record_adapter_call
from sqlalchemy.orm import sessionmaker
from test_stage_control import _fixture

from app.db.models import Artifact, DocumentaryJob, EditLog, PolicyEvent
from app.documentary import jobs as documentary_jobs
from app.documentary.pipeline.base import StageRunner
from app.services.stage_control import StageControlService


def _run_scoped(monkeypatch, db_session, job_id: int, *, extra: bool = False):
    class FixturePipeline(StageRunner):
        async def _touch(self, stage: str, language: str):
            record_adapter_call("fixture", stage, language)
            row = (self.db.query(Artifact)
                   .filter_by(stage_job_id=self.job.id, type=(
                       "spoken_version" if stage == "spoken" else "voice_takes"),
                       language=language).one())
            row.output_hash = f"executed-{stage}-{language}"
            if extra and stage == "spoken" and language == "de":
                other = self.db.query(Artifact).filter_by(
                    stage_job_id=self.job.id, type="spoken_version", language="en").one()
                other.output_hash = "unexpected-outside-preview"
            return {"stage": stage, "language": language}

        async def run(self):
            for language in ("en", "de", "fa", "ar"):
                await self._stage(f"spoken:{language}",
                                  lambda language=language: self._touch("spoken", language))
                await self._stage(f"voice:{language}",
                                  lambda language=language: self._touch("voice", language))
            return {}

    monkeypatch.setattr(documentary_jobs, "DocumentaryPipeline", FixturePipeline)
    monkeypatch.setattr("app.services.artifact_shadow.register_stage_outputs",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.artifacts.ArtifactRegistry.reconcile",
                        lambda *args, **kwargs: None)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    asyncio.run(documentary_jobs.run_job(job_id, session_factory=factory))
    db_session.expire_all()
    edit = db_session.query(EditLog).order_by(EditLog.id.desc()).first()
    return json.loads(edit.result_json), db_session.get(DocumentaryJob, job_id)


def _call_counts(path: Path) -> Counter:
    if not path.exists():
        return Counter()
    return Counter(json.loads(line)["stage"] for line in path.read_text().splitlines())


def test_edit_german_spoken_chapter_executes_only_preview_scope(
    db_session, client, monkeypatch, tmp_path
):
    case, job, roots = _fixture(db_session)
    counter = tmp_path / "adapter-calls.jsonl"
    monkeypatch.setenv("SOUNDNESS_CALL_COUNTER", str(counter))
    before = {language: roots[language].output_hash for language in ("en", "fa", "ar")}
    preview = client.post(f"/api/documentary/{case.id}/stage-control/preview", json={
        "stage": "spoken:de", "action": "edit",
        "edit": {"language": "de", "chapter_index": 0, "chapter_text": "new German chapter"},
    })
    assert preview.status_code == 200, preview.text
    assert set(preview.json()["languages"]) == {"de"}
    monkeypatch.setattr("app.documentary.jobs.launch", lambda _job_id: None)
    applied = client.post(f"/api/documentary/{case.id}/stage-control/apply", json={
        "preview_id": preview.json()["preview_id"],
    })
    assert applied.status_code == 200, applied.text
    result, finished_job = _run_scoped(monkeypatch, db_session, job.id)
    assert result["status"] == "completed"
    assert result["previewed_artifact_ids"]
    assert result["executed_artifact_ids"]
    assert result["changed_outside_scope"] == []
    assert set(result["executed_stage_names"]) == set(preview.json()["stage_names"])
    assert finished_job.status == "completed"
    assert {language: db_session.get(Artifact, roots[language].id).output_hash
            for language in ("en", "fa", "ar")} == before
    assert _call_counts(counter) == Counter({"spoken": 1, "voice": 1})


def test_redo_with_prompt_runs_only_previewed_stage_and_dependents(
    db_session, client, monkeypatch, tmp_path
):
    case, job, _roots = _fixture(db_session)
    counter = tmp_path / "adapter-calls.jsonl"
    monkeypatch.setenv("SOUNDNESS_CALL_COUNTER", str(counter))
    preview = client.post(f"/api/documentary/{case.id}/stage-control/preview", json={
        "stage": "spoken:de", "action": "redo_with_prompt", "prompt": "clearer",
    })
    assert preview.status_code == 200, preview.text
    monkeypatch.setattr("app.documentary.jobs.launch", lambda _job_id: None)
    assert client.post(f"/api/documentary/{case.id}/stage-control/apply", json={
        "preview_id": preview.json()["preview_id"],
    }).status_code == 200
    result, _job = _run_scoped(monkeypatch, db_session, job.id)
    assert result["status"] == "completed"
    assert set(result["executed_stage_names"]) == set(preview.json()["stage_names"])
    assert result["changed_outside_scope"] == []
    assert _call_counts(counter) == Counter({"spoken": 1, "voice": 1})


def test_scope_invariant_detects_injected_outside_dependency_then_passes(
    db_session, client, monkeypatch, tmp_path
):
    case, job, _roots = _fixture(db_session)
    service = StageControlService(db_session)
    preview = service.preview(case.id, "spoken:de", "redo_with_prompt", prompt="retry")
    monkeypatch.setattr("app.documentary.jobs.launch", lambda _job_id: None)
    assert service.apply(preview["preview_id"])["status"] == "queued"
    bad, failed_job = _run_scoped(monkeypatch, db_session, job.id, extra=True)
    assert failed_job.status == "failed"
    assert bad["changed_outside_scope"] == [f"spoken-en-{case.id}"]
    assert db_session.query(PolicyEvent).filter_by(
        production_id=job.id, rule_id="STAGE_CONTROL_SCOPE_CHECKED", decision="blocked"
    ).count() == 1

    case, job, _roots = _fixture(db_session)
    preview = service.preview(case.id, "spoken:de", "redo_with_prompt", prompt="retry")
    assert service.apply(preview["preview_id"])["status"] == "queued"
    good, finished_job = _run_scoped(monkeypatch, db_session, job.id)
    assert finished_job.status == "completed"
    assert good["changed_outside_scope"] == []
