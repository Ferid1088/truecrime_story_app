# Phase 10: Repetition and Automation-Feel Checks

## Command

```text
PYTHONPATH=. python scripts/phase10_repetition_verify.py
```

The five candidate records use real case-6 beat ids, reveal ids, and visual
asset ids. They are passed through the existing `repetition_check` with the
production threshold `max_repeats=1`.

## Real Results

```text
candidate_count: 5
within_batch.passed: True
within_batch.reasons: []
prior_persisted_short_count: 0
cross_channel_check.status: NO_PRIOR_SHORTS
cross_channel_check.passed: True
deliberately_repetitive_batch.passed: False
```

The cross-channel check was not skipped: the database contains zero
`ShortFormConcept` rows, so there are no prior persisted shorts for this
channel or any other channel to compare against. That is reported as
`NO_PRIOR_SHORTS`, not treated as evidence of historical diversity.

The deliberately repetitive two-item batch was rejected on all four fields:

```text
hook_structure repeats: weather-cold-open
cta repeats: Follow the timeline from the first call.
opening_visual repeats: VIS_000001
host_pose repeats: HOST_MEDIUM
```

## Verification

```text
PYTHONPATH=. pytest -q tests/test_longform_phase10.py
1 passed, 1 warning in 0.04s
```

Implementation:

```text
scripts/phase10_repetition_verify.py
tests/test_longform_phase10.py
```

Phase 10 passes within-batch repetition checks and the deliberate rejection
check. Cross-channel historical comparison remains data-limited because no
prior short concepts exist.
