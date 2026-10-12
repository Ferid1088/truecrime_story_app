# Prompt 9H Part 4: generated status and matrix

The committed matrix and status were regenerated from the latest snapshot-diff
records with:

```text
PYTHONPATH=.:tests .venv/bin/python tests/generate_soundness_status.py
```

The record set contains the full Phase 4 records, the full Phase 5 batch, the
research-root record, and the strengthened naming and visual-metadata records.
The stale weak naming record and the stale render-config record were replaced
by the isolated Part 3/Part 2 reruns. The earlier failed render-config run is
preserved in `docs/architecture/experiments/9h-provider-payload-boundary.md`.

`tests/test_soundness_matrix.py::test_sound_type_has_a_passing_strong_snapshot_diff_record`
requires every SOUND type to have a passing snapshot-diff record with a saved
output change or a recorded no-effect edge with a non-empty justification.
`test_regenerating_soundness_outputs_is_deterministic` compares two generated
outputs byte-for-byte. The generated files are:

- `docs/architecture/soundness-matrix.md`
- `config/soundness_status.json`

The generated status currently reports SOUND for all 24 registered artifact
types. This is not a registry/hash claim: each status is backed by the
snapshot-diff records listed in the matrix and checked by the tests above.
