# Prompt 9H R: shared Postgres test-case cleanup

The first cleanup attempt ran after
`tests/test_api.py::test_case_crud_flow`. It passed on SQLite but failed on
Postgres because `case_status_history` and other tables have foreign keys
without ORM delete cascades. The subsequent full Postgres run recorded
`1110 passed, 7 failed, 1 error`; later tests also use fixture case id 1, so
deleting it at module scope was an invalid isolation boundary. That finding is
retained here rather than discarded.

The final implementation is the session-scoped fixture
`tests/conftest.py:148-159`. It keeps the fixture case available to all tests,
computes the exact foreign-key closure from that case, deletes only the
reachable rows after the suite, and commits the cleanup. The final full
Postgres suite then passed:

```text
1117 passed, 1 skipped, 1 warning in 438.80s
```

The final live read-only query after E2E reported `cases=323`,
`documentary_jobs=44`, `provider_calls=10`, `policy_events=107`, no row for
case id 1, and `Test Case Alpha` count zero.

During the mandated restart, the configured Brave Search health check was initially
unavailable and `scripts/dev_up.sh` invoked its native fallback. This was an
environmental setup event outside the application changes; no Brave Search code or
configuration was modified by this project. Subsequent restarts observed the
external service as healthy and did not manage it.
