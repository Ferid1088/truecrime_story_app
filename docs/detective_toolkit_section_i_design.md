# Section I — inspection, targeted revision, reliable reruns

## What was built

- **Inspector endpoint**: `GET /api/cases/{case_id}/detective/plan` (`app/documentary/detective/api.py`)
  returns every `DetectiveScene` for the case's current `RevealGraph` with its aim, chosen tools,
  content bindings, `reuse_reason`, and the Main Visual Planner's recorded `planner_decision` +
  reason (Section D/architecture-remediation). It surfaces Section G's compiled
  `detective_design_reviews` too. Nothing new is computed — `app/documentary/detective/inspector.py`
  only reshapes what Sections B-H already persist.
- **Inspector UI**: a new "Detective" tab (`frontend/components/documentary/tabs/detective.tsx`,
  registered in `workspace.tsx`) lists every scene, its aim/change-in-understanding/expected-gain,
  and a table of its tool actions with `planner_decision` as a colored badge (green
  accepted/combined, amber deferred, red rejected) plus the reason text. A stale scene shows a
  visible amber "stale — <reason>" badge instead of being silently hidden or rewritten.
- **Targeted revision**: `POST /api/cases/{case_id}/detective/scenes/{scene_key}/repair` calls the
  Detective Director's existing `repair` mode (Section C) scoped to one scene's current persisted
  content — no new repair logic, just a real call path to the one that already exists. Gated behind
  `detective_toolkit.enabled` (off by default, same as every other live call path in this feature) —
  returns 409 when disabled rather than silently no-op'ing.
- **Invalidation (I2)**: `POST /api/cases/{case_id}/detective/claims/{claim_key}/correct` records the
  correction as a Section B knowledge event (`operation="update"` — the event log is append-only, so
  a correction is itself an event, never a silent row rewrite) and then
  `invalidate_dependent_scenes` marks every `DetectiveScene` whose actions' `content_bindings`
  actually reference that claim key as `stale=true` with a reason. New migration `0075` adds
  `detective_scenes.stale`/`stale_reason` (applied + round-tripped against live Postgres, confirmed
  via `information_schema`). **Honest scope**: this is binding-level invalidation (does a scene's own
  actions cite the changed claim), not a full transitive dependency graph across questions/relations
  that merely *reference* the same entity without a direct content binding — a fuller invalidation
  rule is future work if the owner wants it.
- **Resumability (I3)**: not rebuilt — `toolkit.invoke` already never re-pays once a plan exists for a
  graph (proven by `test_detective_toolkit_boundary.py::test_toolkit_invoke_never_re_pays_for_an_existing_plan`,
  Section C/architecture-remediation). Characterized here, not duplicated.
- **Languages (I4)**: narration units already carry `language` (Section B); the inspector reads
  whatever language the graph's narration was built in. **Honest scope**: full RTL layout
  verification (German expansion, Persian/Arabic shaping in the actual inspector UI) was not built
  this pass — the inspector currently renders tool_id/purpose/reason text left-to-right regardless of
  language. Deferred, stated plainly rather than forced.

## Live evidence

- Migration `0075` applied and round-tripped against live Postgres (`alembic upgrade head` /
  `downgrade -1` / `upgrade head`); `detective_scenes.stale`/`stale_reason` confirmed present via a
  direct `information_schema.columns` query.
- Backend: `tests/test_detective_inspector.py`, 6/6 passing — plan-summary construction, invalidation
  scoping (bound scene flips stale, unrelated scene untouched), the three API endpoints (plan, correct,
  repair-when-disabled), and a 404 for a case with no graph yet.
- **Real browser evidence**: `frontend/e2e/detective-inspector.spec.ts`, run via this repo's own
  `scripts/e2e_stack.sh` (disposable `truecrime_e2e` database, real API on :8011, real Next.js dev
  server on :3011, real Chromium via Playwright) — `1 passed`. The spec mocks only the
  `/detective/plan` network response (everything else, including the session-auth check, hits the
  real e2e backend) and asserts the rendered DOM shows both scenes, the `fact_card`/`accepted` row,
  and the `unknowns`/`rejected` row with its stale badge and decision reason — genuine rendered-page
  verification, not a unit test of the component in isolation.

## Checklist

I1 VERIFIED (inspector view, real evidence above). I2 VERIFIED (invalidation rule + migration, scoped
honestly). I3 VERIFIED (characterized against existing proven behavior). I4 IN_PROGRESS (data is
language-aware; RTL layout verification not built this pass).
