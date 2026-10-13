"""Production Shorts queue worker for the isolated E2E stack only."""

from __future__ import annotations

import time

from app.db.base import SessionLocal
from app.shortform.pipeline import claim_next_short_job, reap_short_jobs, run_short_queue_job


def main() -> None:
    worker_id = "e2e-shorts-worker"
    while True:
        with SessionLocal() as db:
            reap_short_jobs(db)
            queue_id = claim_next_short_job(db, worker_id, lease_seconds=120)
        if queue_id is not None:
            run_short_queue_job(queue_id)
        else:
            time.sleep(0.1)


if __name__ == "__main__":
    main()
