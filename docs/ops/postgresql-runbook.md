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
disposable-database suite with an explicit administrator URL (normally the
local Homebrew superuser):

```sh
TC_TEST_DATABASE_URL='postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime_test' \
TRUECRIME_TEST_ADMIN_URL='postgresql+psycopg://<local-superuser>@127.0.0.1:5432/postgres' \
PYTHONPATH=. .venv/bin/pytest -q tests/test_sqlite_to_postgres.py
```

When `TRUECRIME_TEST_ADMIN_URL` is omitted, the fixture derives the local
superuser URL from `TC_TEST_DATABASE_URL` and `$USER`; CI or a remote server
must set it explicitly. The administrator URL is used only to create and drop
temporary databases and is never stored in the repository.

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
