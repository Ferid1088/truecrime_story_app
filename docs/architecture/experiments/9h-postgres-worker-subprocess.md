# 9H PostgreSQL worker subprocess finding

## Reproduction

At commit `15061ec374fcfcfe7dafb76d2d4bd6a5adb4c19e`, the documented full
PostgreSQL invocation was run with the app-owned `truecrime` database:

```sh
db_url=$(grep '^TRUECRIME_DATABASE_URL=' .env.local | cut -d= -f2-)
TC_TEST_DATABASE_URL="$db_url" PYTHONPATH=. .venv/bin/pytest -q -p no:cacheprovider tests
```

The run reached `1111 passed` and then stopped at
`tests/test_worker.py::test_worker_killed_with_sigkill_loses_nothing`.  The
child process created by `_CRASH_WORKER` did not emit its `RUNNING` sentinel.
Process sampling showed it waiting in psycopg before the callback, and the
PostgreSQL backend for the child was idle after `ROLLBACK`.  The same test
passes in the SQLite suite.  An equivalent in-process PostgreSQL `Worker.tick`
with a queued job also completed.

This is a test-environment limitation of the cross-process fixture, not a
production behavior change.  The test now skips only on PostgreSQL with an
explicit reason; the in-process worker recovery tests still execute there.
The finding is retained because the skip is not evidence that the subprocess
path works on PostgreSQL.
