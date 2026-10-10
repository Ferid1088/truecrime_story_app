"""Job worker: runs documentary jobs outside the API process.

    JOB_RUNNER=external python -m app.worker        # the worker
    JOB_RUNNER=external uvicorn app.main:app        # the API (only queues jobs)

Restarting the API no longer stops a running film. The worker

* claims queued jobs one at a time (an atomic update, so two workers never take the same job),
* reports in ("heartbeat") while it holds a job,
* finds jobs whose worker died (no heartbeat for the lease time) and queues them again,
  at most `job_max_auto_resumes` times; finished stages keep their saved results and are skipped,
* on SIGTERM / Ctrl-C hands its jobs back to the queue (no retry used) and exits.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DocumentaryJob
from app.documentary.recovery import interrupt_job, reap_stale_jobs, requeue_job
from app.utils import utc_now

log = logging.getLogger("truecrime.worker")


def new_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def claim_next_job(db: Session, worker_id: str) -> int | None:
    """Take the oldest queued job nobody holds. Returns its id, or None."""
    now = utc_now()
    ids = [r[0] for r in db.query(DocumentaryJob.id).filter(
        DocumentaryJob.status == "queued", DocumentaryJob.worker_id.is_(None)
    ).order_by(DocumentaryJob.id).limit(5).all()]
    for job_id in ids:
        result = db.execute(
            update(DocumentaryJob)
            .where(DocumentaryJob.id == job_id, DocumentaryJob.status == "queued",
                   DocumentaryJob.worker_id.is_(None))
            .values(worker_id=worker_id, heartbeat_at=now, updated_at=now))
        db.commit()
        if result.rowcount == 1:  # we won the race
            return job_id
    return None


def beat(db: Session, worker_id: str) -> int:
    """Report that this worker is alive for every job it holds."""
    result = db.execute(
        update(DocumentaryJob)
        .where(DocumentaryJob.worker_id == worker_id,
               DocumentaryJob.status.in_(("queued", "running", "cancelling")))
        .values(heartbeat_at=utc_now()))
    db.commit()
    return result.rowcount


def finish_orphan_cancels(db: Session) -> list[int]:
    """A queued job cancelled before any worker took it never runs: close it."""
    done = []
    for job in db.query(DocumentaryJob).filter(
            DocumentaryJob.status == "cancelling", DocumentaryJob.worker_id.is_(None)).all():
        job.status = "cancelled"
        job.completed_at = job.updated_at = utc_now()
        done.append(job.id)
    db.commit()
    return done


def release_held_jobs(db: Session, worker_id: str) -> list[int]:
    """Shutdown: give every job this worker still holds back to the queue without using a retry."""
    now = utc_now()
    released = []
    for job in db.query(DocumentaryJob).filter(
            DocumentaryJob.worker_id == worker_id,
            DocumentaryJob.status.in_(("queued", "running", "cancelling"))).all():
        was_cancelling = job.status == "cancelling"
        interrupt_job(job, now, "the worker was stopped")
        if not was_cancelling:
            requeue_job(job, now, count_attempt=False)
        job.worker_id = None
        released.append(job.id)
    db.commit()
    return released


class Worker:
    def __init__(self, session_factory=None, run_job=None, worker_id: str | None = None,
                 poll_seconds: float | None = None, lease_seconds: float | None = None,
                 max_auto_resumes: int | None = None, max_parallel: int | None = None):
        from app.db.base import SessionLocal

        self.session_factory = session_factory or SessionLocal
        if run_job is None:
            from app.documentary.jobs import run_job as _run

            run_job = _run
        self.run_job = run_job
        self.worker_id = worker_id or new_worker_id()
        self.poll = settings.worker_poll_seconds if poll_seconds is None else poll_seconds
        self.lease = settings.job_lease_seconds if lease_seconds is None else lease_seconds
        self.max_auto = settings.job_max_auto_resumes if max_auto_resumes is None else max_auto_resumes
        if max_parallel is None:
            from app.core.ai_config import ai_config

            max_parallel = int(ai_config.concurrency.jobs)
        self.max_parallel = max_parallel
        self.stop = asyncio.Event()
        self.tasks: dict[int, asyncio.Task] = {}

    def _db(self) -> Session:
        return self.session_factory()

    async def tick(self) -> None:
        """One round: tidy up, report in, take work if there is room."""
        db = self._db()
        try:
            reap_stale_jobs(db, lease_seconds=self.lease, max_auto_resumes=self.max_auto)
            finish_orphan_cancels(db)
            beat(db, self.worker_id)
            self.tasks = {i: t for i, t in self.tasks.items() if not t.done()}
            while len(self.tasks) < self.max_parallel and not self.stop.is_set():
                job_id = claim_next_job(db, self.worker_id)
                if job_id is None:
                    break
                log.info("worker %s took job %s", self.worker_id, job_id)
                self.tasks[job_id] = asyncio.create_task(self._run(job_id))
        finally:
            db.close()

    async def _run(self, job_id: int) -> None:
        try:
            await self.run_job(job_id, self.session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:  # run_job records its own failures; this is the last line of defence
            log.exception("job %s crashed the runner", job_id)
        finally:
            db = self._db()
            try:
                job = db.get(DocumentaryJob, job_id)
                if job is not None and job.worker_id == self.worker_id and job.status not in (
                        "queued", "running", "cancelling"):
                    job.worker_id = None
                    db.commit()
            finally:
                db.close()

    async def _heartbeat_loop(self) -> None:
        interval = max(0.05, min(self.lease / 3, 30.0))
        while not self.stop.is_set():
            db = self._db()
            try:
                beat(db, self.worker_id)
            finally:
                db.close()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> None:
        heart = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self.stop.is_set():
                await self.tick()
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.poll)
                except asyncio.TimeoutError:
                    pass
        finally:
            for t in self.tasks.values():
                t.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            self.stop.set()
            await heart
            db = self._db()
            try:
                released = release_held_jobs(db, self.worker_id)
            finally:
                db.close()
            if released:
                log.info("worker %s handed back jobs %s", self.worker_id, released)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if settings.job_runner != "external":
        raise SystemExit("Set JOB_RUNNER=external for both the worker and the API, otherwise the API runs the jobs itself.")
    from app.db.schema import init_schema

    init_schema()
    worker = Worker()

    async def go():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, worker.stop.set)
        log.info("worker %s started", worker.worker_id)
        await worker.run()

    asyncio.run(go())


if __name__ == "__main__":
    main()
