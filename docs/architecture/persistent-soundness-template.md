# Phase 3c — real persistent template

Implementation: `tests/soundness_template.py` (`ensure_template`, `restore_template`,
`clone_postgres_template`). The fixture is the real four-language documentary
job with provider fakes and real ffmpeg. No registry hash is used for restore
validation. Manifest identity contains HEAD, tracked Python code hash, full
config-tree hash, mock version, backend, languages, and mode=off. A mismatch
automatically creates a new template; stale copies remain recoverable locally.

SQLite is checkpointed after the child process exits, then restored by file
copy. PostgreSQL uses a dedicated `tc_soundness_*` database and
`CREATE DATABASE target TEMPLATE source`, without terminating any connection.
Both restore the complete media directory and rebase only the absolute harness
root in persisted path strings. No artifact content/hash is replaced.

Commands and observed results (parent `3546939` plus this phase's changes):

```
PYTHONPATH=. .venv/bin/pytest -q -s tests/test_soundness_template.py
4 passed, 1 warning in 44.07s
REAL_TEMPLATE rebuild_seconds=43.413405 restore_seconds=0.209959 snapshot_keys=181

PYTHONPATH=.:tests .venv/bin/python tests/soundness_template.py \
  --postgres-admin-url postgresql+psycopg://feri@127.0.0.1:5432/postgres
rebuild_seconds=43.477324 restore_seconds=0.720993
restored_database=tc_soundness_9136a9fcda084f95bc08e024e6536198
POSTGRES_RESTORE keys=181 diff_count=0

PYTHONPATH=. .venv/bin/pytest -q -s \
  tests/test_visual_production.py::test_four_language_independent_run_determinism
1 passed, 1 warning in 87.44s
INDEPENDENT_DETERMINISM artifacts=181 provider_calls=96 runs=2
snapshot_equal=true logical_request_snapshot_equal=true
```

SQLite restore compares all 181 saved-output keys against the captured baseline.
The independent test uses two fresh child processes, explicit separate database
files, and differently named output directories; ASR requests compare by logical
block/retry identity. These are determinism/restore results, not soundness proof.
No SOUND status is granted by these tests. Neither restore needed optimization
against the ten-second threshold. The local output tree is approximately 105 MB.

Additional restore validation caught two path hazards before perturbations:
in-repo baselines stored repo-relative references, and macOS `/var` aliases could
produce `/private/private/...` during replacement. Baselines now build outside
the repository with a resolved absolute root. The restore test also checks that
asset references resolve to existing files INSIDE the restored directory, not
just that their saved strings hash equally. The CLI checks full snapshot equality
for both backends. Temporary build directories are moved into the template;
old templates are archived locally, not deleted.
