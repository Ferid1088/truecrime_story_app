# Short-Form Engine Phase 3 Loop Log

## Loop A - Iteration 1

- Failure: Focused tests failed because string asset IDs were normalized as dicts.
- Root cause: `evaluate_candidate()` called `.get()` before checking whether an asset entry was a dict.
- Fix: Added `_asset_use()` normalization for string and dict asset inputs.
- Result: `tests/test_shortform_phase3.py` passed: 4 passed.

## Loop A - Iteration 2

- Failure: None.
- Root cause: Not applicable.
- Fix: Reran focused selection tests.
- Result: `tests/test_shortform_phase3.py` passed again: 4 passed.

## Loop B - Iteration 1

- Failure: None.
- Root cause: Not applicable.
- Fix: Added Phase 3 to `scripts/shortform_verify.sh`.
- Result: `scripts/shortform_verify.sh --through 3` passed: 41 passed.

## Loop B - Iteration 2

- Failure: None.
- Root cause: Not applicable.
- Fix: Reran cumulative verification.
- Result: `scripts/shortform_verify.sh --through 3` passed again: 41 passed.

## Gate Status

Passed twice in a row.

Implemented:

- Hard-gate evaluation over generated candidates.
- Soft scoring for survivors only.
- MMR-style diverse selection.
- Dynamic shortfall handling with readable rejection reasons.
- Diversity gate for one-concept-type selected sets.
