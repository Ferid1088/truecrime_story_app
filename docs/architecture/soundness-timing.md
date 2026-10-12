# Soundness harness timing — 9D Part B

Evidence commit: `d0422f47a311ec533006a953680bc810655d3119`.

The full mocked four-language baseline child run used:

```
SOUNDNESS_CHILD=1 SOUNDNESS_RUN_ROOT=/private/tmp/currentfinalB.8PdoKw/run \
SOUNDNESS_CAPTURE=/private/tmp/currentfinalB.8PdoKw/snapshot.json \
SOUNDNESS_PAYLOAD_LOG=/private/tmp/currentfinalB.8PdoKw/mock-payloads.jsonl \
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job
```

Output: `1 passed, 1 warning in 36.97s`; snapshot size: 181 keys.

The restorable template implementation is
`app/services/soundness.py:153-173`:
`create_snapshot_template` copies the SQLite database and the complete output
directory, and `restore_snapshot_template` restores both without consulting
registry hashes. Its round-trip test is
`tests/test_soundness_matrix.py::test_snapshot_template_round_trips_database_copy_and_files`
(lines 38-52).

Measured template round-trip command:

```
env PYTHONPATH=. /usr/bin/time -p .venv/bin/pytest -q \
  tests/test_soundness_matrix.py::test_snapshot_template_round_trips_database_copy_and_files
```

Output: `1 passed, 1 warning in 0.02s`; wall-clock process time `real 1.77s`.
This is below the 10-second optimization threshold.

No Phase 4 perturbation run is claimed by this document. The current
snapshot-template test uses disposable SQLite/files and is not a substitute
for a full perturbation matrix.

