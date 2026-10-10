# Running documentary jobs

## Two ways to run

* **Inline (default).** The API process runs the jobs itself. Restarting the API stops a running film;
  the job is then marked "interrupted" and the Resume button continues it (finished stages are not paid for again).
* **External worker (recommended for long films).** Set `JOB_RUNNER=external` for both processes:

      JOB_RUNNER=external python -m app.worker          # keeps running; Ctrl-C or SIGTERM hands jobs back
      JOB_RUNNER=external uvicorn app.main:app          # the API only queues jobs

  Restarting the API does not touch a running film. Stopping the worker puts its jobs back in the queue;
  starting it again continues them.

## What happens when something dies

* The worker reports in every few seconds while it holds a job (`heartbeat_at`).
* No report for `JOB_LEASE_SECONDS` (default 120): the job is queued again automatically, at most
  `JOB_MAX_AUTO_RESUMES` times (default 2). Stages that finished keep their saved results and are skipped, so
  nothing finished is paid for twice. After the cap the job stays "interrupted" until you press Resume.
* Research jobs (the search engine runs inside the API process) are marked failed after a restart, as before.
