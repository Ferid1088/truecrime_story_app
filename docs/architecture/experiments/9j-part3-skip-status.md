# 9J Part 3 — skip-mode status

The SOUND types after the completed Part 2 evidence refresh are
`master_story`, `naming`, and `research_result`. The committed evidence
therefore keeps enforcement closed for every other type. The real four-language
fixture was not run in verify/enforce mode: its fixture intentionally pins
incremental mode off while it proves the full saved-output snapshot. This
record does not claim that the requested full-pipeline skip matrix passed.

## Executed evidence

Command:

```text
PYTHONPATH=.:tests .venv/bin/pytest -q -s \
  tests/test_skip_policy.py tests/test_scope_preview.py
```

Result: `9 passed`.

The unit-level policy evidence is:

| scenario | result |
|---|---|
| verify unchanged artifact | would-skip; producer still ran; `verify_match` recorded |
| enforce unchanged artifact | stage was reused and marked `status=skipped`, `detail.reused=true` |
| out-of-band stored-output edit | would-run; reason `output hash changed` |
| pending user edit | would-run; reason `pending user edit or lock` |
| dependency rerun | would-run; reason `a dependency ran in this job` |

The API output of the reused stage was:

```json
{"id":5,"stage":{"detail":{"reuse_decision":{"actions":["continue","review"],"code":null,"context":{"artifact_ids":["skip-5"],"job_id":5,"mode":"enforce","stage":"master_story"},"decision":"skip","message":"all hash and policy gates passed","policy":"incremental","reason":"all hash and policy gates passed","recoverable":true,"rule_id":"INCREMENTAL_SKIP","severity":"info"},"reused":true},"name":"master_story","status":"skipped"}}
```

The scope-preview proof also reports no missing snapshot keys for the recorded
perturbations; it is a prediction/API proof, not the missing real-pipeline
byte-compare proof.

## Not completed

The required Part 3 real-pipeline runs for every SOUND type, the four-language
persistent-template unchanged/perturbed counts, stage-call savings, wall-clock
measurements, and the forced-rerun byte comparison were not completed. No
SOUND type was promoted to make those runs appear complete.
