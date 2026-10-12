# Prompt 9I live-database pollution audit

Commit `5a940484` was the starting point. The audit connected read-only to the
configured `TRUECRIME_DATABASE_URL` (`truecrime`) before cleanup. All 323 cases
were created in the same local hour (`2026-10-12 21:00 Europe/Berlin`): 307 had
`origin IS NULL` and 16 had `origin='manual'`. The grouped title query was:

```sql
SELECT coalesce(canonical_title,'<NULL>'), date_trunc('hour',created_at), count(*)
FROM cases GROUP BY 1,2 ORDER BY 2,1;
```

The non-case row groups were: `documentary_jobs`: cancelled 17, interrupted
20, completed 5, failed 1, partial 1; `provider_calls`: 10 HTTP telemetry
rows (HeyGen GET/POST 5, `apimaster.ai` chat 1, `files.example` GET 1, `h`
GET 1, and `studio` GET 2); `policy_events`: 107 rows, including incremental
run/skip/verify records and retry-audit records.

The database schema has no writer PID, process-start, or request-origin column,
so row-level OS-process attribution cannot be reconstructed after the fact.
The evidence establishes the source classes instead:

* The Postgres suite command recorded before this audit targeted the live URL
  (`docs/ops/postgresql-runbook.md` at the pre-9I revision). Its API tests use
  real SQLAlchemy sessions and explain the 323 test-shaped titles.
* The browser E2E fixture intercepts every `/api/**` request at
  `frontend/e2e/fixtures.ts:468`, so the 49 mocked Playwright tests did not
  write these rows. The live-only smoke scripts are separate files
  (`frontend/e2e/smoke-real.mjs` and `frontend/e2e/smoke-resume.mjs`) and were
  not used by the isolated E2E command.
* The 10 provider-call rows are HTTP telemetry. The table has no `provider` or
  `purpose` columns; the exact grouping is by `host, method, path, status`.

Before cleanup, the committed backup was created with `pg_dump --format=custom`
and then SHA-256 checked:

```text
data/backups/truecrime-live-9i-20261012T200140Z.dump
d8b7e9e30edd4763803a819ada0fac83e1c7192773860c9c031034ac3556f1e0
```

`scripts/clean_live_db.py:13-42` preserves only `alembic_version` and
`app_settings`, prints counts for every public table, and executes one explicit
`TRUNCATE ... RESTART IDENTITY CASCADE` over all other public tables. The
observed before/after headline counts were:

| table | before | after |
|---|---:|---:|
| cases | 323 | 0 |
| documentary_jobs | 44 | 0 |
| provider_calls | 10 | 0 |
| policy_events | 107 | 0 |
| artifacts | 299 | 0 |
| artifact_deps | 516 | 0 |
| app_settings | 0 | 0 (preserved) |
| alembic_version | 1 | 1 (preserved) |

The complete per-table JSON was emitted by the cleanup command and retained in
the Part 1 command log. The test guard is implemented at
`app/core/test_database_guard.py:10-43` and installed before any Postgres
schema reset at `tests/conftest.py:51-54`; its regression tests are
`tests/test_database_guard.py:6-26`.
