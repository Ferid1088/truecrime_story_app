"""Offline production-worker proofs for Prompt 10G's isolation contract."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from app.core import media_tools
from app.db.models import (
    Artifact,
    Case,
    DocumentaryJob,
    EditLog,
    EditorialBlueprint,
    ShortCandidate,
    StoryVersion,
    Video,
)
from app.documentary.jobs import plan_stages
from app.providers.generation.base import GenerationResult
from app.shortform import export, pipeline
from app.shortform.agents import MockShortsTextAdapter
from app.shortform.selection import evaluate_candidate


def _job(db, tmp_path, status="completed"):
    suffix = db.query(Case).count() + 1
    case = Case(canonical_title=f"Shorts proof {suffix}", slug=f"shorts-proof-{suffix}", language="en")
    db.add(case)
    db.flush()
    story = StoryVersion(case_id=case.id, version=1, kind="final", narrative_angle="evidence",
                         language="en", story_text="A finished evidence-led story.", status="approved")
    db.add(story)
    db.flush()
    db.add(StoryVersion(case_id=case.id, version=1, kind="localized", language="de",
                        narrative_angle="evidence", story_text="Die Akte legt die letzte Zeit fest.",
                        status="approved"))
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
    rows = db_session.query(pipeline.RenderedShort).filter_by(film_job_id=job.id).all()
    assert result["renders"] == len(rows)
    assert {row.language for row in rows} == {"en", "de"}
    assert all((tmp_path / row.video_path).exists() for row in rows)
    stages = {row["name"]: row for row in json.loads(job.stages_json)}
    assert stages["short_selection"]["status"] == "done"
    assert stages["short_render:en"]["status"] == "done"
    assert stages["short_render:de"]["status"] == "done"


def test_short_failure_does_not_change_long_film_state(db_session, tmp_path, monkeypatch):
    job = _job(db_session, tmp_path)
    film = db_session.query(Video).filter_by(job_id=job.id, language="en").one()
    long_film = tmp_path / "long-film.mp4"
    long_film.write_bytes(b"finished long film bytes")
    before_hash = hashlib.sha256(long_film.read_bytes()).hexdigest()
    film.file_path = str(long_film)
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
    assert film.file_path == str(long_film)
    assert hashlib.sha256(long_film.read_bytes()).hexdigest() == before_hash


def test_shorts_do_not_start_before_finished_or_after_failed(db_session, tmp_path):
    for status in ("running", "failed"):
        job = _job(db_session, tmp_path, status=status)
        assert pipeline.produce_for_job(db_session, job)["status"] == "skipped"
        assert db_session.query(pipeline.ShortCandidate).filter_by(film_job_id=job.id).count() == 0


def test_real_film_render_fails_closed_without_caption_capability(tmp_path, monkeypatch):
    source = tmp_path / "finished-film.mp4"
    source.write_bytes(b"stand-in media")
    monkeypatch.setattr(export, "ffmpeg_caption_capability", lambda: {
        "available": False, "reason": "FFmpeg lacks libass/subtitles",
        "fix": "install ffmpeg-full",
    })
    with pytest.raises(export.FFmpegCapabilityError, match="FFmpeg lacks libass"):
        export.build_export_package(
            tmp_path / "short", source_video=source, narration=["Caption required"],
        )


def test_caption_capability_health_reports_binary_and_filters(monkeypatch):
    expected = {
        "available": True, "executable": "/configured/ffmpeg", "ffprobe": "/configured/ffprobe",
        "subtitles_filter": True, "ass_filter": True, "libass": True,
        "harfbuzz": True, "fribidi": True, "reason": None, "fix": None,
    }
    monkeypatch.setattr(export, "ffmpeg_caption_capability", lambda: expected)
    from app.api.system import health

    assert health()["short_form"]["caption_burn_in"] == expected


def test_media_tool_environment_overrides_typed_config(monkeypatch):
    monkeypatch.setenv("FFMPEG_BIN", "/tmp/test-ffmpeg")
    monkeypatch.setenv("FFPROBE_BIN", "/tmp/test-ffprobe")
    assert media_tools.ffmpeg_bin() == "/tmp/test-ffmpeg"
    assert media_tools.ffprobe_bin() == "/tmp/test-ffprobe"


def test_caption_capability_failure_marks_each_language_short_failed(db_session, tmp_path, monkeypatch):
    job = _job(db_session, tmp_path)
    source = tmp_path / "finished-film.mp4"
    source.write_bytes(b"stand-in media")
    original_status = job.status
    monkeypatch.setattr(pipeline, "_film_source", lambda _film: source)
    monkeypatch.setattr(pipeline, "build_export_package", export.build_export_package)
    monkeypatch.setattr(export, "ffmpeg_caption_capability", lambda: {
        "available": False, "reason": "FFmpeg lacks libass/subtitles",
        "fix": "install ffmpeg-full",
    })

    result = pipeline.produce_for_job(db_session, job)

    db_session.refresh(job)
    rows = db_session.query(pipeline.RenderedShort).filter_by(film_job_id=job.id).all()
    assert result["errors"]
    assert rows and {row.language for row in rows} == {"en", "de"}
    assert all(row.status == "failed" and "FFmpeg lacks libass" in row.error for row in rows)
    assert all(row.video_path is None for row in rows)
    assert job.status == original_status == "completed"


def test_finished_job_is_persisted_in_durable_queue(db_session):
    job = _job(db_session, Path("/tmp"))
    row = pipeline.enqueue_for_finished_job(db_session, job.id)
    assert row is not None
    assert row.stage == "selection"
    assert row.status == "queued"
    assert db_session.query(pipeline.ShortProductionJob).filter_by(film_job_id=job.id).count() == 1


def test_caption_edit_scope_rebuilds_only_one_language_short(db_session, tmp_path, monkeypatch):
    job = _job(db_session, tmp_path)
    monkeypatch.setattr(pipeline.ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(pipeline.storage, "rel", lambda path: str(Path(path).resolve().relative_to(tmp_path)))
    monkeypatch.setattr(pipeline, "build_export_package", _fake_export)
    pipeline.produce_for_job(db_session, job)
    target = (db_session.query(pipeline.RenderedShort)
              .filter_by(film_job_id=job.id, language="de").first())
    assert target is not None
    candidate = db_session.get(ShortCandidate, target.candidate_id)
    assert candidate is not None
    artifacts = (db_session.query(Artifact)
                 .filter_by(stage_job_id=job.id, type="short_render", language="de")
                 .all())
    artifact = next((row for row in artifacts
                     if json.loads(row.scope_json or "{}").get("short_id") == candidate.stable_key), None)
    assert artifact is not None
    before = {row.id: row.content_hash for row in db_session.query(pipeline.RenderedShort)
              .filter_by(film_job_id=job.id).all()}
    edit = EditLog(
        actor="test", scope_type="short_form", scope_id=str(target.id), status="previewed",
        scope_preview_json=json.dumps({
            "job_id": job.id, "artifact_ids": [artifact.id],
            "stage_names": ["short_render:de"], "action": "edit",
            "edit": {"caption": "A revised German caption."},
        }), result_json="{}",
    )
    db_session.add(edit)
    db_session.commit()

    pipeline._update_scoped_stage(db_session, edit)

    after = {row.id: row.content_hash for row in db_session.query(pipeline.RenderedShort)
             .filter_by(film_job_id=job.id).all()}
    assert after[target.id] != before[target.id]
    assert {key: value for key, value in after.items() if key != target.id} == {
        key: value for key, value in before.items() if key != target.id
    }


def _run_queued_selection(db_session, job, monkeypatch):
    selection = pipeline.enqueue_for_finished_job(db_session, job.id)
    assert selection is not None
    claimed = pipeline.claim_next_short_job(db_session, "test-selection", film_job_id=job.id)
    assert claimed == selection.id
    assert pipeline.run_short_queue_job(claimed) ["status"] == "queued"
    return db_session.query(pipeline.ShortProductionJob).filter_by(
        film_job_id=job.id, stage="script_render").order_by(pipeline.ShortProductionJob.id).all()


def test_provider_adapter_counter_and_native_language_payload(db_session, tmp_path, monkeypatch):
    job = _job(db_session, tmp_path)
    monkeypatch.setattr(pipeline.ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(pipeline.storage, "rel", lambda path: str(Path(path).resolve().relative_to(tmp_path)))
    monkeypatch.setattr(pipeline, "build_export_package", _fake_export)
    adapter = MockShortsTextAdapter()

    result = pipeline.produce_for_job(db_session, job, adapter=adapter)

    assert result["status"] == "completed"
    assert adapter.call_counts["shorts.write"] == adapter.call_counts["shorts.audit"]
    assert adapter.call_counts["shorts.write"] == result["renders"]
    scripts = db_session.query(pipeline.ShortFormScript).filter(
        pipeline.ShortFormScript.language == "de").all()
    assert scripts and all("Die Akte" in script.body for script in scripts)


def test_reveal_firewall_rejects_a_short_that_spoils(db_session):
    result = evaluate_candidate({
        "allowed_reveals": ["last-known-time"],
        "text_reveals": ["case-outcome"],
        "claims": [], "asset_details": [], "hook": "What happened?", "cta": "Learn more",
        "duration": 30,
    })
    assert result["passed_hard_gates"] is False
    assert result["gate_results"]["RevealFirewall"]["passed"] is False


def test_durable_queue_auto_start_and_restart_reuses_rows(db_session, tmp_path, monkeypatch):
    job = _job(db_session, tmp_path)
    monkeypatch.setattr(pipeline.ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(pipeline.storage, "rel", lambda path: str(Path(path).resolve().relative_to(tmp_path)))
    monkeypatch.setattr(pipeline, "build_export_package", _fake_export)
    adapter = MockShortsTextAdapter()
    monkeypatch.setattr(pipeline, "ProviderShortsTextAdapter", lambda: adapter)

    queue_rows = _run_queued_selection(db_session, job, monkeypatch)
    first = queue_rows[0]
    assert pipeline.claim_next_short_job(db_session, "test-crashed-worker", film_job_id=job.id) == first.id
    db_session.refresh(first)
    first.heartbeat_at = first.updated_at - timedelta(seconds=pipeline.SHORT_QUEUE_LEASE_SECONDS + 1)
    db_session.commit()
    assert pipeline.reap_short_jobs(db_session, lease_seconds=1) == [first.id]
    reclaimed = pipeline.claim_next_short_job(db_session, "test-restarted-worker", film_job_id=job.id)
    assert reclaimed == first.id
    assert pipeline.run_short_queue_job(reclaimed)["status"] == "done"
    for row in queue_rows[1:]:
        assert pipeline.claim_next_short_job(db_session, "test-worker", film_job_id=job.id) == row.id
        assert pipeline.run_short_queue_job(row.id)["status"] == "done"
    db_session.expire_all()
    assert db_session.query(pipeline.ShortProductionJob).filter_by(film_job_id=job.id, status="done").count() == len(queue_rows) + 1
    assert db_session.query(pipeline.RenderedShort).filter_by(film_job_id=job.id).count() == len(queue_rows)
    assert len({row.content_hash for row in db_session.query(pipeline.RenderedShort).filter_by(film_job_id=job.id)}) == len(queue_rows)


def test_running_or_failed_film_never_enqueues_shorts(db_session, tmp_path):
    for status in ("running", "failed"):
        job = _job(db_session, tmp_path, status=status)
        assert pipeline.enqueue_for_finished_job(db_session, job.id) is None
        assert db_session.query(pipeline.ShortProductionJob).filter_by(film_job_id=job.id).count() == 0


def test_durable_failure_isolated_from_film_and_other_language(db_session, tmp_path, monkeypatch):
    job = _job(db_session, tmp_path)
    film = db_session.query(Video).filter_by(job_id=job.id, language="en").one()
    long_film = tmp_path / "finished-long-film.mp4"
    long_film.write_bytes(b"immutable long-film bytes")
    before = hashlib.sha256(long_film.read_bytes()).hexdigest()
    film.file_path = str(long_film)
    db_session.commit()
    monkeypatch.setattr(pipeline.ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(pipeline.storage, "rel", lambda path: str(Path(path).resolve().relative_to(tmp_path)))
    def fail_german(output_dir: Path, **kwargs):
        if kwargs["language"] == "de":
            raise RuntimeError("injected durable Shorts failure")
        return _fake_export(output_dir, **kwargs)
    monkeypatch.setattr(pipeline, "build_export_package", fail_german)
    adapter = MockShortsTextAdapter()
    monkeypatch.setattr(pipeline, "ProviderShortsTextAdapter", lambda: adapter)

    queue_rows = _run_queued_selection(db_session, job, monkeypatch)
    for row in queue_rows:
        assert pipeline.claim_next_short_job(db_session, "failure-test-worker", film_job_id=job.id) == row.id
        pipeline.run_short_queue_job(row.id)

    db_session.refresh(job)
    assert job.status == "completed"
    assert hashlib.sha256(long_film.read_bytes()).hexdigest() == before
    assert db_session.query(pipeline.RenderedShort).filter_by(film_job_id=job.id, language="en", status="done").count() > 0
    assert db_session.query(pipeline.RenderedShort).filter_by(film_job_id=job.id, language="de", status="failed").count() > 0


class _RecordedProvider:
    """Recorded structured responses used to exercise the provider adapter offline."""

    name = "recorded-provider"

    def __init__(self, writer, auditor, *, cost=0.01):
        self.writer = writer
        self.auditor = auditor
        self.cost = cost
        self.calls = []

    def is_configured(self):
        return True

    async def check_health(self):
        return True

    async def generate_text(self, role, system, user, images=None):  # pragma: no cover - structured only
        raise AssertionError(f"unexpected text generation for {role}")

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append(role)
        payload = self.writer if role == "chapter_writer" else self.auditor
        return payload, GenerationResult(
            text=json.dumps(payload), model="recorded-model", provider=self.name,
            input_tokens=100, output_tokens=50, total_tokens=150, cost_usd=self.cost,
            generation_id=f"recorded-{len(self.calls)}",
        )


def _recorded_queue_row(db_session, tmp_path, provider, monkeypatch, *, cap=None, language=None):
    job = _job(db_session, tmp_path)
    if cap is not None:
        from app.db.models import AppSetting

        db_session.add(AppSetting(key=f"shorts:cost_cap:{job.case_id}",
                                  value_json=json.dumps({"max_cost_usd": cap})))
        db_session.commit()
    monkeypatch.setattr(pipeline.ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(pipeline.storage, "rel", lambda path: str(Path(path).resolve().relative_to(tmp_path)))
    monkeypatch.setattr(pipeline, "build_export_package", _fake_export)
    rows = _run_queued_selection(db_session, job, monkeypatch)
    row = next(row for row in rows if language is None or row.language == language)
    from app.utils import utc_now

    row.status, row.worker_id, row.attempts = "running", "recorded-worker", row.attempts + 1
    row.heartbeat_at = row.lease_until = utc_now()
    db_session.commit()
    result = pipeline._process_short_job(
        db_session, db_session.get(pipeline.ShortProductionJob, row.id),
        adapter=pipeline.ProviderShortsTextAdapter(provider),
    )
    db_session.refresh(row)
    db_session.refresh(job)
    return job, row, result


def test_recorded_provider_empty_answer_is_failed_with_persisted_reason(db_session, tmp_path, monkeypatch):
    provider = _RecordedProvider({}, {"approved": True})
    _, row, result = _recorded_queue_row(db_session, tmp_path, provider, monkeypatch, language="en")
    assert result["status"] == row.status == "failed"
    assert "incomplete" in row.error
    assert row.provider_cost_usd == pytest.approx(0.03)
    assert row.provider_call_count == 3


def test_recorded_provider_wrong_language_is_failed_with_persisted_reason(db_session, tmp_path, monkeypatch):
    writer = {
        "hook": "What happened next?",
        "body": "The evidence shows the final timeline and the record contradicts the first account clearly.",
        "cta": "Watch the full story.",
        "captions": [{"start": 0.1, "end": 4.0, "text": "The evidence shows the final timeline."}],
    }
    provider = _RecordedProvider(writer, {"approved": True})
    job, row, result = _recorded_queue_row(db_session, tmp_path, provider, monkeypatch)
    assert result["status"] == row.status == "failed"
    assert "requested language de" in row.error
    assert job.status == "completed"


def test_recorded_provider_over_length_answer_is_failed_with_persisted_reason(db_session, tmp_path, monkeypatch):
    body = " ".join(["evidence"] * 106)
    writer = {
        "hook": "What happened next?", "body": body, "cta": "Watch the full story.",
        "captions": [{"start": 0.1, "end": 4.0, "text": "Evidence."}],
    }
    provider = _RecordedProvider(writer, {"approved": True})
    _, row, result = _recorded_queue_row(
        db_session, tmp_path, provider, monkeypatch, language="en"
    )
    assert result["status"] == row.status == "failed"
    assert "over-length" in row.error


def test_provider_cost_cap_skips_short_without_blocking_long_film(db_session, tmp_path, monkeypatch):
    writer = {
        "hook": "What happened next?",
        "body": "The evidence shows the final timeline and the record contradicts the first account clearly.",
        "cta": "Watch the full story.",
        "captions": [{"start": 0.1, "end": 4.0, "text": "The evidence shows the final timeline."}],
    }
    provider = _RecordedProvider(writer, {"approved": True}, cost=0.01)
    job, row, result = _recorded_queue_row(
        db_session, tmp_path, provider, monkeypatch, cap=0.015, language="en"
    )
    assert result["status"] == row.status == "skipped", row.error
    assert "cost cap reached" in row.error
    assert row.provider_cost_usd == pytest.approx(0.02)
    assert row.provider_call_count == 2
    assert job.status == "completed"
    assert db_session.query(pipeline.RenderedShort).filter_by(film_job_id=job.id, status="done").count() == 0


def test_postgres_worker_death_requeues_and_renders_once(db_session, tmp_path, monkeypatch):
    from app.db.base import engine

    if engine.dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL row-locking and a real worker process")
    job = _job(db_session, tmp_path)
    monkeypatch.setattr(pipeline.ai_config.documentary, "storage_dir", str(tmp_path / "cases"))
    monkeypatch.setattr(pipeline.storage, "rel", lambda path: str(Path(path).resolve().relative_to(tmp_path)))
    monkeypatch.setattr(pipeline, "build_export_package", _fake_export)
    monkeypatch.setattr(pipeline, "ProviderShortsTextAdapter", lambda: MockShortsTextAdapter())
    rows = _run_queued_selection(db_session, job, monkeypatch)
    queue = rows[0]

    database_url = engine.url.render_as_string(hide_password=False)
    child_code = f"""
import os, sys, time
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
os.environ['TRUECRIME_DATABASE_URL'] = {database_url!r}
os.environ['DATABASE_URL'] = {database_url!r}
from app.db.base import SessionLocal
from app.shortform.pipeline import claim_next_short_job
db = SessionLocal()
claimed = claim_next_short_job(db, 'doomed-process', lease_seconds=0.2, film_job_id={job.id})
print(f'CLAIMED {{claimed}}', flush=True)
time.sleep(600)
"""
    env = os.environ.copy()
    env["TRUECRIME_DATABASE_URL"] = database_url
    env["DATABASE_URL"] = database_url
    proc = subprocess.Popen(
        [sys.executable, "-c", child_code], cwd=Path(__file__).resolve().parents[1],
        env=env, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == f"CLAIMED {queue.id}"
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    db_session.expire_all()
    db_session.refresh(queue)
    assert queue.status == "running" and queue.attempts == 1
    time.sleep(0.35)
    assert pipeline.reap_short_jobs(db_session, lease_seconds=0.1) == [queue.id]
    assert pipeline.claim_next_short_job(db_session, "rescued-process", lease_seconds=1, film_job_id=job.id) == queue.id
    outcome = pipeline.run_short_queue_job(queue.id)
    db_session.refresh(queue)
    assert outcome["status"] == queue.status == "done"
    assert queue.attempts == 2
    assert db_session.query(pipeline.RenderedShort).filter_by(
        film_job_id=job.id, candidate_id=queue.candidate_id, language=queue.language
    ).count() == 1
