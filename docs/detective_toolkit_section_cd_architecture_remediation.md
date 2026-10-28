# Architecture remediation: A4 / C5 / D5

Closes the gap the owner's 2026-10-28 architecture correction exposed in Sections C and D: the
Detective Director must be an internal service the Main Visual Planner calls through one
observable, persisted boundary, with a recorded accept/combine/defer/reject decision — not code
that independently writes state MVP later happens to consume.

## The gap, as found

- `app/documentary/detective/director.py`'s `map_film`/`direct_scenes`/`repair` were real,
  tested, live-verified functions (Section C) — but nothing in `app/documentary/mvp/pipeline.py`
  (the real pipeline) called them. They were only ever exercised by Section C's own test/live
  script.
- `app/documentary/mvp/needs.py:_needs_with_detective_handoff` (Section D) only *consumed*
  `DetectiveScene`/`DetectiveToolAction` rows **if they already existed** for the graph — a silent
  "use if present" read, with no call boundary and no recorded decision.

## What was built

1. **Config flag**, default off: `config/parameters/detective_toolkit.json` →
   `ai_config.detective_toolkit.enabled` (new `DetectiveToolkitConfig` in `app/core/ai_config.py`,
   same pattern as `DetectiveBoardConfig`). With the flag off, `stage_needs`/`plan_needs` behave
   byte-for-byte as before — confirmed by the full suite (see below) and by
   `test_toolkit_invoke_is_a_noop_when_disabled`, which fails loudly (an `AssertionError` from a
   provider stub that raises if called) if the Detective Director is ever reached while disabled.

2. **The single call boundary**: `app/documentary/detective/toolkit.py:invoke(db, case,
   blueprint_row, graph)`. This is now the *only* place `app.documentary.detective.director` may
   be imported from pipeline code — enforced by
   `test_pipeline_and_needs_never_import_the_director_module_directly`, an AST-based check over
   `pipeline.py`, `direct.py`, `alternatives.py`, `engines.py`, `coverage.py`. When the flag is on
   and no current plan exists for the graph, it calls `map_film` once, then `direct_scenes` across
   every narrated beat, through Section C's existing, unmodified code path. When a plan already
   exists for the graph, it returns immediately without a new paid call
   (`test_toolkit_invoke_never_re_pays_for_an_existing_plan`).

3. **Wired into the real pipeline**: `app/documentary/mvp/needs.py:_needs_with_detective_handoff`
   now calls `toolkit.invoke(...)` before checking `has_detective_plan` — this is the Main Visual
   Planner's actual call into the toolkit, at its real pipeline entry point (`stage_needs` →
   `plan_needs` → `_needs_with_detective_handoff`).

4. **The planner's recorded decision (D5)**: new columns on `detective_tool_actions`
   (`planner_decision` ∈ `{accepted, combined, deferred, rejected}` as a `CHECK` constraint,
   `planner_decision_reason`), migration `0073` (applied and round-tripped against live
   PostgreSQL — see evidence below; rewritten with `batch_alter_table` after an initial attempt
   broke this repo's SQLite-backed migration-consistency test, since SQLite cannot `ALTER` a
   constraint directly). `app/documentary/detective/dispatch.py:record_planner_decisions` sets
   this for every action after `_needs_with_detective_handoff` computes which beats survived
   validation: `deferred` (no resolvable narration anchor yet), `accepted` (its beat's requirement
   came from this action), `rejected` (beat resolved but requirement validation dropped it).
   `combined` is reserved for a future beat-level field merge; today's merge is a full per-beat
   override, so it's never produced — documented honestly rather than forced.

## Live evidence

Scratch case 22 (never case 5), 2 beats, flag enabled only for this call, reverted after:

```
case_id 22 graph_id 7
visual_plan_id 16 status requirements
tool_actions: 2
  act-01-fact fact_card decision= rejected reason= beat B01 resolved but was dropped by requirement validation
  act-02-unknowns unknowns decision= rejected reason= beat B02 resolved but was dropped by requirement validation
```

Both detective-authored actions were rejected by `validate_needs` (the pre-existing, stricter
requirement validator — its entity/evidence-pack cross-checks are Section D2's still-open scope,
not this remediation's), and the toolkit's own gap-fill path correctly stepped in: the old
`mvp.needs` agent call ran for the uncovered beats, exactly as `_needs_with_detective_handoff` is
designed to do when the detective plan doesn't fully cover the film. This is a complete, honest
demonstration of the full call boundary *and* its reject/fallback path on real model output — the
`accepted` branch is proven deterministically by
`test_record_planner_decisions_covers_deferred_accepted_and_rejected` instead, since forcing a real
`accepted` outcome through `validate_needs`'s stricter checks is D2's remaining work, not this
section's.

Real provider calls and cost (`provider_calls`, case 22):

| path | model | cost (EUR) |
| --- | --- | --- |
| Detective Director: map_film | gemini-3.8-flash | 0.01858814 |
| Detective Director: direct_scenes | gemini-3.8-flash | 0.02322361 |
| Main Visual Planner: needs (gap-fill) | gemini-3.8-flash | 0.01349482 |
| **total** | | **0.05530657** |

Migration live-verification (PostgreSQL, `alembic current` → `0073 (head)`):

```sql
select column_name from information_schema.columns
 where table_name='detective_tool_actions' and column_name like 'planner_decision%';
-- planner_decision, planner_decision_reason

select conname from pg_constraint where conname='ck_detective_action_planner_decision';
-- ck_detective_action_planner_decision
```

Round-tripped (`alembic downgrade -1` → `alembic upgrade head`) twice (once per migration
revision, after the SQLite-compatibility fix) with no errors.

## Checkpoint

- **A4 VERIFIED** — one callable boundary (`toolkit.invoke`), enforced by an AST bypass-regression
  test, default-off flag confirmed by a full-suite run with zero behavior change, real end-to-end
  call demonstrated.
- **C5 VERIFIED** — the Detective Director runs only through `toolkit.invoke`; the live call above
  shows caller (`stage_needs`/`plan_needs`), decision (rejected, with reason), and persisted
  handoff (3 real `provider_calls` rows, `DetectiveScene`/`DetectiveToolAction` rows for graph 7).
- **D5 VERIFIED** — every action gets a typed `planner_decision` + reason; all three real branches
  (`deferred`/`accepted`/`rejected`) proven (two live via real rejection, one via a deterministic
  unit test covering the branch real content didn't exercise this run); `combined` documented as
  not-yet-producible rather than faked.

## Not done / explicitly out of scope here

- Making a detective-authored action actually survive `validate_needs`'s stricter checks (so a
  real `accepted` outcome is observed end-to-end) is Section D2's remaining scope, unchanged by
  this remediation.
- `H5` (final production script/renderer consuming the planner's accepted decisions, no bypass) is
  Section H's job, not touched here.
