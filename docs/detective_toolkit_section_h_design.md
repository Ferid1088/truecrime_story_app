# Section H — integrate the plan into production and rendering

## H5: a real bypass, fixed

`app/documentary/detective/dispatch.py:resolve_dispatchable_actions` dispatched any tool action
with a builder and resolvable beat/data, regardless of `DetectiveToolAction.planner_decision`
(the `accepted`/`combined`/`deferred`/`rejected` field the architecture remediation added for
D5). A `rejected` or still-undecided (`None`) action could reach a real shot via
`place_detective_graphics`, wired into `app/documentary/production/script.py:compose()` — exactly
the unrecorded downstream override H5 exists to prevent.

Fixed: `resolve_dispatchable_actions` now skips (`"skip": "not_accepted_by_planner"`) any action
whose `planner_decision` is not `accepted` or `combined`, before even checking for a builder.
Four existing test fixtures (`test_detective_section_d.py`, `test_detective_section_f.py`) built
`DetectiveToolAction` rows without setting `planner_decision`, so they started failing under the
fix — correctly, since they were unknowingly relying on the bypass. Updated those fixtures to set
`planner_decision="accepted"` (the realistic precondition for what they actually test: dispatch
mechanics for an action the planner has approved), and added dedicated regression tests in
`tests/test_detective_section_h.py` for all four decision states.

## H1: detective intent survives composition

`tests/test_detective_section_h.py::test_dispatched_graphic_survives_finalize_graphics_H1` proves
a detective-placed shot's `graphic.type`/`graphic.data` are unchanged by
`app/documentary/graphics/shots.py:finalize` (cue-fraction resolution, Section A's obstacle 7),
and the shot is not among `finalize`'s dropped list. Host insertion, credit/label overlays, and
final encoding are not separately re-tested here — they operate on the shot list generically, not
on a shot's graphic payload, and were already exercised (unaffected) by the existing full suite.

## H2: version consistency, honestly scoped

No design-review result is persisted anywhere yet keyed by content/text version — Section G's
`review()` makes a fresh call every time with whatever `text_version`/data it's given. There is
therefore no cache that could silently return a stale result today.
`test_no_hidden_staleness_because_nothing_is_cached_yet_H2` proves this directly: two
`build_input` calls with different `text_version`/data never bleed into each other. This is a
real but narrow proof — it rules out a staleness *bug*, not a staleness *solution*. Full
invalidation when an earlier correction changes a scene's text/assets/timing (the doc's own
"version and cache by text, language, relevant assets... an earlier correction must invalidate
later dependent states") is Section I's explicit job ("Add inspection, targeted revision, and
reliable reruns"), not H2's — flagged as `IN_PROGRESS` rather than forced.

## The design-review wiring seam (new)

`app/documentary/detective/design_review.py:review_resolved_actions(db, case_id, resolved,
language, text_version=...)` is the real, gated call path from production into the Visual Design
Director: for every non-skip resolved action, it renders the actual frame and calls `review()`,
returning outcomes. Gated by the same `ai_config.detective_toolkit.enabled` flag as
`toolkit.invoke` (default **off**) — a real case's pipeline makes zero extra calls or cost unless
the owner turns the flag on. Wired into the one real call site,
`app/documentary/production/script.py:build_production_script` (already `async def`), right after
`resolve_dispatchable_actions`; outcomes are attached to the script dict as
`detective_design_reviews` (empty list when disabled or no actions). This is informational only —
it does not change shot placement; acting on a `revise`/`needs_attention` finding by routing a
repair is Section J's job (the Film Experience Reviewer), which does not exist yet.

## H3/H4: honestly blocked on Section J

"Findings route to the correct owner, produce a repair, and close only after the new output
passes" (H3) and "new regressions reopen checks; exhausted settings leave a resumable incomplete
state" (H4) both require the Film Experience Reviewer's repair-routing logic (Section J,
dependencies H-I — i.e. J depends on this section, not the reverse). There is nothing to route
findings *to* yet. Left `NOT_STARTED` with `dependencies: ["J"]` noted, rather than forcing a
shallow characterization against machinery that doesn't exist.

## Evidence

- `tests/test_detective_section_h.py`: 8 tests, all passing (H5 bypass fix ×2 parametrized cases,
  H1 survival, H2 no-staleness characterization, design-review gating no-op).
- Full suite: run before and after this section's changes (see commit message for exact counts).
- No live paid LLM call was needed for this section's own tests — the design-review wiring is
  proven as a no-op when disabled (the real, honest default); a live gated call exercising a real
  `review()` round-trip through this new seam already happened in Section G's own evidence
  (`docs/detective_toolkit_section_g_design.md`) on the same `design_review.review()` function.
