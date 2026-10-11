# 0002 Separate worker for long documentary jobs
**Context.** Jobs run for minutes to hours and used to live inside the API process; restarting the API killed them silently.
**Decision.** Jobs are rows in the database. `JOB_RUNNER=inline` keeps the old single-process mode; `external` makes the API only queue, and `python -m app.worker` claims jobs with a lease, heartbeats, finishes or hands them back, and resumes crashed ones (finished stages are reused) a bounded number of times.
**Consequences.** The database is the queue: no broker to run. One more process to deploy (compose does it). Lease length trades failure-detection speed against false takeovers.
