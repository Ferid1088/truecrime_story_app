# Local PostgreSQL runbook

The app uses the Homebrew `postgresql@16` service and the app-owned
`truecrime` database by default. SQLite is retained for unit tests only; tests
must select it explicitly with `TRUECRIME_DATABASE_URL=sqlite:///...`.

## Start and check

```sh
scripts/pg_up.sh
scripts/pg_up.sh --status
scripts/dev_up.sh --first-run-safe
scripts/dev_up.sh --status
```

`pg_up.sh` starts PostgreSQL only when it is not already ready, creates the
`truecrime` and `truecrime_test` databases if absent, and stores the generated
password in the ignored `.env.local`. It never prints the password. The app
startup path runs `alembic upgrade head` against the configured PostgreSQL
URL; a PostgreSQL failure is fatal and never falls back to SQLite
(`scripts/dev_up.sh:254-261`). `pg_down.sh` stops PostgreSQL only when
`pg_up.sh` created the `data/dev/postgresql.started` marker.

## Keg-only FFmpeg for Shorts

Production Shorts require the Homebrew `ffmpeg-full` build so captions are
burned with libass. It is intentionally not linked into PATH. Configure the
absolute tools for both API and worker processes with either environment
variables or the typed production config:

```sh
export FFMPEG_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
export FFPROBE_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffprobe
scripts/dev_up.sh --first-run-safe
```

`config/parameters/documentary.json` carries the same defaults, and
`scripts/dev_up.sh` passes them to API/worker when the keg exists. The
exporters and health check resolve these paths through
`app/core/media_tools.py`; they do not modify PATH and do not run `brew link`.
A missing `subtitles`/`ass` filter, libass, HarfBuzz, FriBidi, or ffprobe fails
a production short closed with a diagnostic instead of producing an
uncaptioned video.

## Durable Shorts queue

When a documentary job reaches `completed` or `completed_warnings`, the API
only inserts the selection row in `short_production_jobs`. The normal external
worker (`TRUECRIME_JOB_RUNNER=external python -m app.worker`) claims selection
and per-language script/render rows with a lease and heartbeat. A worker
restart requeues an expired lease; the candidate/language unique key and render
content hash prevent duplicate rows or output directories. Shorts failures are
recorded on their own queue/render rows and never change the finished film job.

## Back up and verify a dump

```sh
scripts/db_backup_pg.sh
```

This writes a custom-format dump and a sibling `.sha256` file under the ignored
`data/backups/` directory. It restores the dump into an app-named temporary
database, compares every public-table row count, and drops that temporary
database before returning.

## Restore the PostgreSQL backup

Restore a dump with standard PostgreSQL tooling into an explicitly named
temporary database first. Do not use a SQLite restore for a PostgreSQL dump.
The backup script already performs this check; keep the dump and checksum
together for an operator restore.

## Roll back to the last SQLite backup

This is a deliberate fallback for the test-data SQLite file, not a data
migration. It stops this app, removes SQLite WAL sidecars through the existing
SQLite backup API, restores the specified verified SQLite backup, changes the
ignored local URL to SQLite, and starts safe mode:

```sh
scripts/db_backup_pg.sh
scripts/db_rollback.sh --to-sqlite data/backups/truecrime-final-sqlite-<timestamp>.db
```

The PostgreSQL service and its databases are not removed by this rollback.
Return to PostgreSQL by setting `TRUECRIME_DATABASE_URL` in `.env.local` to the
app-owned PostgreSQL URL and starting `scripts/dev_up.sh --first-run-safe`.

## Postgres integration tests

The app role `truecrime` is deliberately not a database administrator. Run the
disposable-database suite; the fixture derives a local administrator from
`$PGUSER`/`$USER`:

```sh
TC_TEST_DATABASE_URL='postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime_test' \
PYTHONPATH=. .venv/bin/pytest -q tests/test_sqlite_to_postgres.py
```

Run the full suite against the app-owned PostgreSQL test database with:

```sh
TC_TEST_DATABASE_URL='postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime_test' \
PYTHONPATH=. .venv/bin/pytest -q -p no:cacheprovider tests
```

The fixture refuses `truecrime` and any other non-disposable PostgreSQL
database unless `TRUECRIME_ALLOW_LIVE_TESTS=1` is explicitly set. The app
role is not a database administrator; the fixture derives a local admin URL
only for resetting the disposable `truecrime_test` schema.

Run browser E2E in an isolated stack with:

```sh
scripts/e2e_stack.sh
```

This creates and drops `truecrime_e2e`, uses API port 8011 and frontend port
3011, and never calls `dev_up.sh`, `dev_down.sh`, or manages SearXNG.

The full suite has one intentional PostgreSQL-only skip for the cross-process
SIGKILL fixture; the reason and limitation are recorded in
`docs/architecture/experiments/9h-postgres-worker-subprocess.md`.

Set `TRUECRIME_TEST_ADMIN_URL` for a remote or nonstandard server. When no
admin URL is usable, the fixture prints one `POSTGRES_TEST_ADMIN_SKIP` line and
skips only these disposable-database tests. The administrator URL is used only
to create and drop temporary databases and is never stored in the repository.

## Table-count convention

The current PostgreSQL schema has 64 public base tables including
`alembic_version`, or 63 when that migration bookkeeping table is excluded.
The earlier Phase-C report counted the 55 pre-existing application tables only;
it excluded the eight tables added by reconciliation (`app_settings`,
`artifact_deps`, `artifacts`, `backup_runs`, `edit_log`, `override_log`,
`policy_events`, and `provider_calls`) and `alembic_version`. The two checks are:

```sql
SELECT count(*) FROM information_schema.tables
 WHERE table_schema = 'public' AND table_type = 'BASE TABLE';       -- 64
SELECT count(*) FROM information_schema.tables
 WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
   AND table_name <> 'alembic_version';                              -- 63
```

No migration after `0010` created an additional application table; the apparent
55-versus-64 difference is the counting scope above.

## Remove the Prompt 8P smoke case

The disposable smoke case was removed from the app-owned `truecrime` database
with the following explicit SQL after confirming that `cases` contained only
ID 1. `CASCADE` is required because the smoke case had one status-history row;
it does not affect any other database:

```sql
TRUNCATE TABLE cases RESTART IDENTITY CASCADE;
```

## Intentional live-database cleanup

Before cleaning the app-owned live database, create a custom-format backup and
record its checksum:

```sh
stamp=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump --format=custom --file="data/backups/truecrime-live-${stamp}.dump" "$TRUECRIME_DATABASE_URL"
shasum -a 256 "data/backups/truecrime-live-${stamp}.dump"
```

The cleanup preserves only `alembic_version` (migration state) and
`app_settings` (operator settings). It truncates the remaining public
application tables with `RESTART IDENTITY CASCADE` and prints every table's
before/after count. The exact command is:

```sh
PYTHONPATH=. .venv/bin/python scripts/clean_live_db.py \
  --database-url "$TRUECRIME_DATABASE_URL" \
  --confirm CLEAN_TRUECRIME_LIVE
```

The Prompt 9I run records the generated output and checksum in its findings
document; tests must never target this database.
