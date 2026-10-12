"""Part 9K: inspect one persistent four-language job after a real rerun.

The shell runner owns the disposable DB/output copies.  This test deliberately
does not create a new job: it reruns the completed full four-language job so
the incremental policy sees the saved artifacts from the first run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

# Reuse the exact real-pipeline provider fixture; it is defined beside the
# four-language production test rather than in conftest.py.
from test_visual_production import documentary_env as documentary_env_fixture  # noqa: F401,E402

from app.core.ai_config import ai_config
from app.db.models import (
    Artifact,
    Case,
    DocumentaryJob,
    EditLog,
    PolicyEvent,
    StoryVersion,
    VoicePerformance,
)
from app.documentary import jobs as documentary_jobs
from app.services.soundness import saved_output_snapshot


@pytest.fixture(name="documentary_env")
def _documentary_env(documentary_env_fixture):  # noqa: F811
    return documentary_env_fixture


def _snapshot(db):
    root = Path(os.environ["SOUNDNESS_RUN_ROOT"])
    return saved_output_snapshot(
        db,
        file_roots=[root / name for name in ("cases", "audio", "lib", "intros")],
    )


def _provider_calls(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        provider = json.loads(line)["provider"]
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def _perturb(db, scenario: str) -> None:
    if scenario == "unchanged":
        return
    if scenario == "naming":
        case = db.query(Case).one()
        case.canonical_title = f"{case.canonical_title} [9K naming]"
    elif scenario == "spoken_de":
        row = db.query(StoryVersion).filter_by(kind="spoken", language="de").one()
        structure = json.loads(row.narrative_structure or "{}")
        section = structure["sections"][0]
        marker = " 9K spoken-version edit"
        section["text"] = section["text"] + marker
        row.story_text = row.story_text + marker
        row.narrative_structure = json.dumps(structure, ensure_ascii=False)
        import hashlib

        row.text_hash = hashlib.sha256(row.story_text.encode()).hexdigest()
    elif scenario == "one_voice_take":
        row = db.query(VoicePerformance).filter_by(language="de").order_by(
            VoicePerformance.id.desc()
        ).first()
        assert row is not None
        payload = json.loads(row.performance_json or "{}")
        payload["9k_edit"] = "voice take perturbation"
        row.performance_json = json.dumps(payload, ensure_ascii=False)
    else:
        raise ValueError(scenario)
    db.commit()


def _force_all_stages(job: DocumentaryJob) -> None:
    stages = json.loads(job.stages_json or "[]")
    for stage in stages:
        stage["status"] = "pending"
        stage["detail"] = None
        stage.pop("finished_at", None)
        stage.pop("started_at", None)
    job.stages_json = json.dumps(stages, ensure_ascii=False)
    job.status = "queued"
    job.error = None
    job.result_json = "{}"


def _stage_summary(stages: list[dict]) -> dict[str, str]:
    return {stage["name"]: stage["status"] for stage in stages}


def _policy_events(db, job_id: int, after_id: int) -> list[dict]:
    rows = db.query(PolicyEvent).filter(
        PolicyEvent.production_id == job_id,
        PolicyEvent.id > after_id,
        PolicyEvent.policy == "incremental",
    ).order_by(PolicyEvent.id).all()
    return [
        {
            "stage": json.loads(row.context_json or "{}").get("stage"),
            "decision": row.decision,
            "reason": row.reason,
            "rule_id": row.rule_id,
        }
        for row in rows
    ]


def test_9k_rerun_real_job(isolated_pipeline_db, db_session, documentary_env, client, monkeypatch):
    job = db_session.query(DocumentaryJob).order_by(DocumentaryJob.id).first()
    assert job is not None
    assert json.loads(job.languages_json) == ["en", "de", "fa", "ar"]

    scenario = os.environ.get("K_SCENARIO", "unchanged")
    gate = os.environ.get("K_GATE")
    mode = os.environ.get("K_MODE", "enforce")
    force = os.environ.get("K_FORCE", "0") == "1"
    monkeypatch.setattr(ai_config.production_policies.incremental, "mode", mode)
    monkeypatch.setattr(
        ai_config.production_policies.incremental,
        "enforced_types",
        ["research_result", "master_story", "naming"],
    )
    _perturb(db_session, scenario)
    if gate == "out_of_band":
        row = db_session.query(StoryVersion).filter_by(kind="master").one()
        edited_text = row.story_text + " [9K out-of-band output edit]"
        edited_hash = hashlib.sha256(edited_text.encode("utf-8")).hexdigest()
        db_session.execute(
            text("UPDATE story_versions SET story_text=:story_text, text_hash=:text_hash "
                 "WHERE id=:id"),
            {"story_text": edited_text, "text_hash": edited_hash, "id": row.id},
        )
        db_session.commit()
        db_session.expire_all()
    elif gate == "pending_edit":
        row = db_session.query(Artifact).filter_by(
            stage_job_id=job.id, type="master_story"
        ).one()
        db_session.add(EditLog(scope_id=row.id, status="running", actor="9K"))
        db_session.commit()
    if force:
        _force_all_stages(job)
        db_session.commit()

    before_snapshot = _snapshot(db_session)
    # End the read transaction opened by the snapshot before the job's own
    # SessionLocal connection evaluates out-of-band output drift.
    db_session.rollback()
    before_event = db_session.query(PolicyEvent).order_by(PolicyEvent.id.desc()).first()
    before_event_id = before_event.id if before_event else 0
    log_path = Path(os.environ.get("SOUNDNESS_PAYLOAD_LOG", ""))
    before_calls = _provider_calls(log_path)

    started = __import__("time").perf_counter()
    asyncio.run(documentary_jobs.run_job(job.id))
    wall_clock = __import__("time").perf_counter() - started
    db_session.expire_all()
    job = db_session.get(DocumentaryJob, job.id)
    assert job is not None
    stages = json.loads(job.stages_json or "[]")
    after_snapshot = _snapshot(db_session)
    after_calls = _provider_calls(log_path)
    provider_delta = {
        provider: after_calls.get(provider, 0) - before_calls.get(provider, 0)
        for provider in set(before_calls) | set(after_calls)
    }
    events = _policy_events(db_session, job.id, before_event_id)
    api = None
    if os.environ.get("K_API") == "1":
        response = client.get(f"/api/documentary/jobs/{job.id}")
        api = response.json() if response.status_code == 200 else {
            "status_code": response.status_code,
            "body": response.text,
        }
    output = {
        "scenario": scenario,
        "mode": mode,
        "force": force,
        "job_id": job.id,
        "stages": stages,
        "stage_status": _stage_summary(stages),
        "events": events,
        "policy_stage_decisions": {
            stage: next((event["decision"] for event in events
                         if event["stage"] == stage
                         and event["rule_id"] in {"INCREMENTAL_SKIP", "INCREMENTAL_RUN"}),
                         None)
            for stage in (event["stage"] for event in events
                          if event["rule_id"] in {"INCREMENTAL_SKIP", "INCREMENTAL_RUN"})
        },
        "provider_calls": provider_delta,
        "wall_clock_seconds": round(wall_clock, 3),
        "before_snapshot": before_snapshot,
        "after_snapshot": after_snapshot,
        "pending_edits": db_session.query(EditLog).filter(
            EditLog.status.in_(("previewed", "running"))
        ).count(),
        "api": api,
    }
    print("NINE_K_RERUN", json.dumps(output, sort_keys=True))
    capture = os.environ.get("K_OUTPUT")
    if capture:
        Path(capture).write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
