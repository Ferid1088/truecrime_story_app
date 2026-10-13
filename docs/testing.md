# Test commands

The normal backend suite runs against SQLite. To run it against a disposable
PostgreSQL database, set `TC_TEST_DATABASE_URL` to a database whose name is
explicitly test-scoped; the test guard rejects the live database.

The bounded 9K incremental-skip proof is intentionally separate because it
creates a persistent soundness template and several disposable rerun copies.
CI or a nightly/on-demand job can run it with:

```sh
PYTHONPATH=. .venv/bin/python tests/run_9k_skip_proof.py
```

The command exits non-zero on any proof mismatch and prints the evidence root
for the generated report. It must use disposable SQLite copies and must not
inherit the live `DATABASE_URL`.
