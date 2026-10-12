# Prompt 9I failed isolation experiment: canonical URL precedence

The first post-cleanup suite attempt used a disposable `TC_TEST_DATABASE_URL`,
but the parent environment still contained `TRUECRIME_DATABASE_URL` for the
live `truecrime` database. `app/core/config.py:6-9` intentionally gives that
canonical variable precedence over the legacy `DATABASE_URL` alias. The
fixture only set `DATABASE_URL` at `tests/conftest.py:54-78`, so application
settings continued to open the live engine.

Evidence: after the run, the live counts were `cases=323`,
`documentary_jobs=44`, `provider_calls=183`, and `policy_events=125`. The
SQLite and Postgres output looked green, but this live-count check proved the
run was invalid. This is retained as a finding; it is not treated as a green
isolation result.

The fix sets `TRUECRIME_DATABASE_URL` and `DATABASE_URL` to the guarded target
for PostgreSQL, and removes the canonical variable for SQLite, before
`app.db.base` is imported (`tests/conftest.py:58-83`). The guard itself remains
at `app/core/test_database_guard.py:10-43`. The suites are being rerun after
the live database is backed up and cleaned again.
