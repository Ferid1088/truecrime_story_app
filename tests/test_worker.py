"""The job worker: claims once, reports in, notices a dead worker, hands jobs back on shutdown."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from app.db.base import SessionLocal
from app.db.models import Case, DocumentaryJob
from app.documentary.recovery import recover_after_restart, reap_stale_jobs
from app.utils import utc_now
from app import worker as W

ROOT = Path(__file__).resolve().parents[1]
STAGES = [{"name": "blueprint", "status": "done", "detail": {"blueprint_id": 1}},
          {"name": "audio_plan", "status": "running", "detail": None, "attempts": 1}]


def _case(db) -> Case:
    title = f"Worker {uuid.uuid4().hex[:8]}"
    case = Case(canonical_title=title, slug=title.lower().replace(" ", "-"), language="en")
    db.add(case)
    db.commit()
    return case


def _job(db, status="queued", **kw) -> DocumentaryJob:
    job = DocumentaryJob(case_id=_case(db).id, status=status, stages_json=json.dumps(STAGES), **kw)
    db.add(job)
    db.commit()
    return job


@pytest.fixture(autouse=True)
def _clean_queue(db_session):
    """The test database is shared: start every test with no live jobs."""
    for j in db_session.query(DocumentaryJob).filter(
            DocumentaryJob.status.in_(("queued", "running", "cancelling"))).all():
        j.status = "cancelled"
    db_session.commit()


# --- claiming -------------------------------------------------------------------------------


def test_each_job_is_claimed_once_in_order(db_session):
    a, b = _job(db_session), _job(db_session)
    assert W.claim_next_job(db_session, "w1") == a.id
    assert W.claim_next_job(db_session, "w2") == b.id
    assert W.claim_next_job(db_session, "w3") is None
    db_session.refresh(a)
    assert a.worker_id == "w1" and a.heartbeat_at is not None


def test_two_workers_racing_for_one_job_only_one_wins(db_session):
    from concurrent.futures import ThreadPoolExecutor

    job = _job(db_session)

    def claim(name):
        db = SessionLocal()
        try:
            return W.claim_next_job(db, name)
        finally:
            db.close()

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(claim, [f"w{i}" for i in range(8)]))
    assert results.count(job.id) == 1 and results.count(None) == 7


def test_a_cancelled_or_running_job_is_never_claimed(db_session):
    _job(db_session, status="cancelling")
    _job(db_session, status="running")
    assert W.claim_next_job(db_session, "w") is None


# --- dead workers ---------------------------------------------------------------------------


def test_job_of_a_live_worker_is_left_alone(db_session):
    job = _job(db_session, status="running", worker_id="w", heartbeat_at=utc_now())
    rep = reap_stale_jobs(db_session, lease_seconds=120, max_auto_resumes=2)
    assert rep == {"requeued": [], "interrupted": []}
    db_session.refresh(job)
    assert job.status == "running"


def test_job_of_a_dead_worker_is_queued_again_and_keeps_finished_stages(db_session):
    job = _job(db_session, status="running", worker_id="dead", heartbeat_at=utc_now() - timedelta(minutes=10))
    rep = reap_stale_jobs(db_session, lease_seconds=120, max_auto_resumes=2)
    assert rep["requeued"] == [job.id]
    db_session.refresh(job)
    assert job.status == "queued" and job.worker_id is None and job.auto_resumes == 1
    stages = {s["name"]: s for s in json.loads(job.stages_json)}
    assert stages["blueprint"]["status"] == "done"
    assert stages["audio_plan"]["status"] == "pending"
    assert stages["audio_plan"]["errors"][-1]["type"] == "Interrupted"


def test_retries_are_capped_then_the_job_waits_for_a_person(db_session):
    job = _job(db_session, status="running", worker_id="dead", auto_resumes=2,
               heartbeat_at=utc_now() - timedelta(minutes=10))
    rep = reap_stale_jobs(db_session, lease_seconds=120, max_auto_resumes=2)
    assert rep["interrupted"] == [job.id]
    db_session.refresh(job)
    assert job.status == "interrupted" and job.worker_id is None and "stopped responding" in job.error


def test_a_job_claimed_but_never_started_is_also_recovered(db_session):
    job = _job(db_session, status="queued", worker_id="dead", heartbeat_at=utc_now() - timedelta(minutes=10))
    assert reap_stale_jobs(db_session, lease_seconds=120, max_auto_resumes=2)["requeued"] == [job.id]


def test_a_dead_workers_cancelling_job_is_cancelled_not_retried(db_session):
    job = _job(db_session, status="cancelling", worker_id="dead", heartbeat_at=utc_now() - timedelta(minutes=10))
    reap_stale_jobs(db_session, lease_seconds=120, max_auto_resumes=2)
    db_session.refresh(job)
    assert job.status == "cancelled"


def test_a_job_cancelled_while_queued_is_closed(db_session):
    job = _job(db_session, status="cancelling")
    assert W.finish_orphan_cancels(db_session) == [job.id]
    db_session.refresh(job)
    assert job.status == "cancelled" and job.completed_at is not None


# --- shutdown and restart -------------------------------------------------------------------


def test_shutdown_hands_jobs_back_without_using_a_retry(db_session):
    job = _job(db_session, status="running", worker_id="me", heartbeat_at=utc_now(), auto_resumes=1)
    assert W.release_held_jobs(db_session, "me") == [job.id]
    db_session.refresh(job)
    assert job.status == "queued" and job.worker_id is None and job.auto_resumes == 1


def test_api_restart_leaves_jobs_alone_when_a_worker_owns_them(db_session):
    job = _job(db_session, status="running", worker_id="w", heartbeat_at=utc_now())
    recover_after_restart(db_session, include_documentary_jobs=False)
    db_session.refresh(job)
    assert job.status == "running"
    recover_after_restart(db_session)  # the old single-process behaviour is unchanged
    db_session.refresh(job)
    assert job.status == "interrupted"


def test_launch_only_queues_when_the_runner_is_external(db_session, monkeypatch):
    from app.core.config import settings
    from app.documentary import jobs as J

    started = []
    monkeypatch.setattr(J.asyncio, "create_task", lambda coro: (coro.close(), started.append(1))[1])
    monkeypatch.setattr(settings, "job_runner", "external")
    J.launch(1)
    assert started == []
    monkeypatch.setattr(settings, "job_runner", "inline")
    J.launch(1)
    assert started == [1]


def test_resume_clears_worker_bookkeeping(client, db_session, monkeypatch):
    from app.documentary import jobs as J

    monkeypatch.setattr(J, "launch", lambda _id: None)
    job = _job(db_session, status="interrupted", worker_id="old", auto_resumes=2,
               heartbeat_at=utc_now())
    assert client.post(f"/api/documentary/jobs/{job.id}/resume").status_code == 200
    db_session.refresh(job)
    assert job.status == "queued" and job.worker_id is None and job.auto_resumes == 0


# --- the worker loop ------------------------------------------------------------------------


def test_worker_runs_a_queued_job_to_completion_and_lets_go(db_session):
    job = _job(db_session)
    ran = []

    async def fake_run(job_id, session_factory=None):
        db = session_factory()
        j = db.get(DocumentaryJob, job_id)
        j.status = "running"
        db.commit()
        await asyncio.sleep(0.05)
        j.status = "completed"
        db.commit()
        db.close()
        ran.append(job_id)

    async def go():
        w = W.Worker(run_job=fake_run, worker_id="t", poll_seconds=0.02, lease_seconds=3, max_parallel=2)
        task = asyncio.create_task(w.run())
        for _ in range(200):
            await asyncio.sleep(0.02)
            if ran:
                await asyncio.sleep(0.1)
                break
        w.stop.set()
        await task

    asyncio.run(go())
    db_session.expire_all()
    j = db_session.get(DocumentaryJob, job.id)
    assert ran == [job.id] and j.status == "completed" and j.worker_id is None


def test_worker_stopped_mid_job_returns_the_job_to_the_queue(db_session):
    job = _job(db_session)
    started = asyncio.Event

    async def long_run(job_id, session_factory=None):
        db = session_factory()
        j = db.get(DocumentaryJob, job_id)
        j.status = "running"
        db.commit()
        db.close()
        await asyncio.sleep(30)

    async def go():
        w = W.Worker(run_job=long_run, worker_id="t2", poll_seconds=0.02, lease_seconds=3)
        task = asyncio.create_task(w.run())
        for _ in range(200):
            await asyncio.sleep(0.02)
            db = SessionLocal()
            running = db.get(DocumentaryJob, job.id).status == "running"
            db.close()
            if running:
                break
        w.stop.set()
        await task

    asyncio.run(go())
    db_session.expire_all()
    j = db_session.get(DocumentaryJob, job.id)
    assert j.status == "queued" and j.worker_id is None and j.auto_resumes == 0


# --- a real crash ---------------------------------------------------------------------------

_CRASH_WORKER = textwrap.dedent("""
    import asyncio, os, sys
    sys.path.insert(0, {root!r})
    os.environ["DATABASE_URL"] = {url!r}
    from app import worker as W
    from app.db.models import DocumentaryJob

    async def stuck(job_id, session_factory=None):
        db = session_factory()
        j = db.get(DocumentaryJob, job_id)
        j.status = "running"
        db.commit()
        db.close()
        print("RUNNING", flush=True)
        await asyncio.sleep(600)

    async def go():
        w = W.Worker(run_job=stuck, worker_id="doomed", poll_seconds=0.05, lease_seconds=3)
        await w.run()

    asyncio.run(go())
""")


def test_worker_killed_with_sigkill_loses_nothing(db_session):
    """kill -9 in the middle of a job; a second worker takes over and finishes it."""
    job = _job(db_session)
    from app.db.base import engine

    url = engine.url.render_as_string(hide_password=False)  # the database this test run really uses
    proc = subprocess.Popen([sys.executable, "-c", _CRASH_WORKER.format(root=str(ROOT), url=url)],
                            stdout=subprocess.PIPE, text=True, cwd=ROOT)
    try:
        line = proc.stdout.readline()
        assert "RUNNING" in line
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    db_session.expire_all()
    j = db_session.get(DocumentaryJob, job.id)
    assert j.status == "running" and j.worker_id == "doomed"  # the crash left it exactly like this

    rep = reap_stale_jobs(db_session, lease_seconds=3, max_auto_resumes=2, now=utc_now() + timedelta(seconds=60))
    assert rep["requeued"] == [job.id]
    done = []

    async def finish(job_id, session_factory=None):
        db = session_factory()
        x = db.get(DocumentaryJob, job_id)
        x.status = "completed"
        db.commit()
        db.close()
        done.append(job_id)

    async def go():
        w = W.Worker(run_job=finish, worker_id="rescuer", poll_seconds=0.02, lease_seconds=3)
        task = asyncio.create_task(w.run())
        for _ in range(200):
            await asyncio.sleep(0.02)
            if done:
                break
        w.stop.set()
        await task

    asyncio.run(go())
    db_session.expire_all()
    j = db_session.get(DocumentaryJob, job.id)
    assert done == [job.id] and j.status == "completed" and j.auto_resumes == 1
