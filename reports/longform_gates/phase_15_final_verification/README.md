# Phase 15: Final Verification

## Full Backend Loop

Command, run three consecutive times:

```text
PYTHONPATH=. pytest -q
```

Results:

| Run | Result | Duration |
| --- | --- | --- |
| 1 | 844 passed, 2 warnings | 130.03s |
| 2 | 844 passed, 2 warnings | 129.12s |
| 3 | 844 passed, 2 warnings | 131.39s |

The warnings are the existing Starlette `BlockingPortal` deprecation warning
and the existing SQLAlchemy identity-map warning in
`tests/test_host.py::test_host_director_plans_remembers_and_writes`.

## Browser Verification

```text
cd frontend && npm run lint
```

Result: exit 0, no ESLint warnings.

```text
cd frontend && npm run build
```

Result: exit 0. Next.js compiled, TypeScript completed, and 14 static pages
were generated.

```text
cd frontend && npm run test:e2e
```

Result: `33 passed (46.4s)`.

The one stale settings test expectation was updated to use the real Settings
tab and current save workflow. The focused repair also passed:
`1 passed (3.5s)`.

## Gate Coverage

The repository does not have `pytest-cov` or the `coverage` command installed.
Coverage was measured with Python's standard-library `trace` module using the
full relevant long-form and short-form test surface, including the new
Phase 15 branch-coverage tests:

```text
PYTHONPATH=. python -m trace --count --missing \
  --coverdir=reports/longform_gates/phase_15_trace_final4 .venv/bin/pytest -q \
  tests/test_longform_phase*.py tests/test_shortform_phase*.py \
  tests/test_shortform_operations.py tests/test_phase8_rights.py \
  tests/test_longform_phase14.py tests/test_phase15_gate_coverage.py \
  tests/test_thumbnails.py
```

That expanded relevant surface returned `79 passed, 1 warning in 8.33s`.
The trace invocation also printed three pre-existing `pysbd` invalid-escape
syntax warnings; the test runner's single warning was the Starlette deprecation.

| Module | Covered lines | Executable lines | Trace coverage |
| --- | ---: | ---: | ---: |
| `app.longform.compression` | 160 | 166 | 96.4% |
| `app.longform.epistemic` | 253 | 253 | 100.0% |
| `app.longform.rollout` | 73 | 73 | 100.0% |
| `app.longform.service` | 527 | 552 | 95.5% |
| `app.shortform.gates` | 164 | 169 | 97.0% |
| `app.shortform.metrics` | 35 | 35 | 100.0% |
| `app.shortform.operations` | 105 | 106 | 99.1% |
| `app.shortform.publishers` | 33 | 33 | 100.0% |
| `app.shortform.api` | 46 | 46 | 100.0% |
| `app.shortform.director` | 171 | 171 | 100.0% |
| `app.shortform.selection` | 169 | 169 | 100.0% |
| `app.shortform.export` | 113 | 114 | 99.1% |

These are trace line-execution numbers for the selected gate tests, not a
claim of exhaustive branch coverage. No measured gate module remains below
the 95% line-coverage threshold. The remaining 25 unexecuted executable lines
in `service.py` and one in `export.py` are defensive/rare paths; the tests
now exercise the service validation and CRUD error surface rather than merely
the happy path. The raw `.cover` files are in
`reports/longform_gates/phase_15_trace_final4/`.

The original low numbers came from an incomplete selection: the Phase 2/3/3a/4
short-form tests were omitted, and no tests exercised the epistemic extraction
internals or many director, service, rollout, compression, API, selection,
and export branches. The added tests are explicitly labeled synthetic branch
coverage in `tests/test_phase15_gate_coverage.py`; the real case-6 persistence,
graph, contract, and adversarial tests remain in the phase-specific suites.

## Case Readiness

The real database query returned:

```text
case_id | title | blueprint_rows | visual_assets
1 | The Lighthouse Keeper Vanishing | 0 | 0
2 | The Nannup Four | 0 | 0
3 | The Disappearance of Musa al-Sadr | 0 | 0
4 | The Disappearance of Inga Gehricke | 0 | 0
5 | The Caleb Flynn Murder of Ashley Flynn | 0 | 0
6 | The Karmelo Anthony Murder Case | 1 | 32
```

No second-episode run was possible: case 6 is the only case with usable
blueprint and asset data.

## Final Limitations

- Case 6 has no original media segments, so narrator pause/overlap remains a
  documented known gap.
- Platform publishers remain explicit stubs; only export handoff is real.
- No approved short concepts are persisted yet; Phase 11 remains a dry run.
- Phase 13 metrics are mocked and attribution is unavailable.
- Cases 1-5 are not rollout candidates until blueprints and assets exist.
- The two backend warnings above remain non-blocking and were not suppressed.
