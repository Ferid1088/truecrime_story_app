# Monitoring

Two views of the same data: the **Monitoring** page in the left sidebar (the whole app, `GET /api/monitoring`) and the
**Monitoring** tab of one case (`GET /api/cases/{id}/monitoring`). On the page, buttons that open a workspace tab go to
`/documentary/{case}?tab=<tab>`; it lists every active job and every case whose newest job stopped in the last
`recent_days`.

## The case tab

`Documentary > <case> > Monitoring` shows what the app is doing now and what needs the owner. It refreshes every
`monitoring.refresh_seconds` (config/parameters/monitoring.json) and can be paused.

Source: `GET /api/cases/{id}/monitoring` (`app/documentary/monitoring.py`). One snapshot:

- **What needs you** - findings, worst first, each with buttons that lead to the page that solves it (a workspace tab, the
  picture review, Operations, Settings) or act on the job (Resume, Cancel). Findings: stopped or failed job (with its
  next action), a job waiting for picture review, moments with nothing to show, a film waiting at the owner-review gate,
  a silent worker (no heartbeat for `job_lease_seconds`), a stage running longer than `stuck_stage_minutes`, a job
  queued longer than `queued_wait_seconds`, cost cap reached or past `budget_warn_fraction`, an outside service failing
  more than `provider_error_rate` of its calls, a missing search key.
- **Running now / queue** - every active job of the app with progress, stage, worker heartbeat and the call in progress.
- **Pipeline** - the case's latest job, stage by stage with time and attempts; a click opens the stage's tab
  (`stage_tabs` in the config).
- **Services, cost, outside services** - API, database, worker, search key; spend against the cap; calls and failures per
  host in the last `provider_window_minutes`.

Findings are about this case's jobs; running and queued jobs of other cases are listed but do not raise findings here.
