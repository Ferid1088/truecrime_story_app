# Section D — executable detective tools: design and evidence

Scope note up front: this pass delivers a real, tested, gated subset rather than full dispatch
wiring for all 20 tools. Everything here is additive and gated on `has_detective_plan` (a
`DetectiveScene` existing for the blueprint's `RevealGraph`) — **no real case has one today**, so
every change in this document is behaviorally invisible to current production. Where something
was descoped, it says so explicitly rather than claiming more than was built.

## 1. The adapter (`app/documentary/detective/tool_catalog.py`)

Unchanged from Section C's provisional version in shape (`build_tool_catalog()` still returns the
same `list[{tool_id, purpose, input_contract, availability}]` the Detective Director's prompt
input needs), but it is now also the place `dispatch.py` looks up renderer kind -> legacy
`function`/`alternatives` vocabulary (`_FUNCTION_BY_TOOL` in `dispatch.py`). A fuller descriptor
(typed input schema objects, asset requirements, language/layout capabilities as structured
fields rather than prose) is still open work — see "Not done" below.

## 2. Dispatch (`app/documentary/detective/dispatch.py`)

Two DB-dependent resolvers plus one pure placer, split because `production/script.py:compose()`
is a documented pure function (no DB handle) — the same reason `board=` is pre-fetched by
`compose()`'s caller instead of queried inside it:

- `resolve_dispatchable_actions(db, graph_id)`: for each `DetectiveToolAction`, resolves its beat
  (via the scene's first covered `NarrationUnit`) and builds real renderer data. Returns a
  decision record per action (`placed` / `skip: <reason>`), which is itself the doc's required
  "aim -> chosen tool -> reason -> result" record, visible in `compose()`'s output under
  `detective_graphics`.
- `place_detective_graphics(shots, spans, language, resolved)`: pure. Carves each resolved
  action's graphic into its beat's span using the renderer's own `Graphic.can_render`/
  `required_cues` contract and the existing `_carve` mechanism `insert_detective_board_cards`
  already uses — no parallel insertion path.
- Only `fact_card` and `floor_plan` have real data builders (`BUILDERS` dict) in this pass. Every
  other `tool_id` resolves to `skip: not_dispatched_in_this_pass` — an honest, visible skip, not a
  silent drop or a fabricated placement.

## 3. The needs.py hand-off seam (`app/documentary/mvp/needs.py:_needs_with_detective_handoff`)

This is Section A's obstacle: `plan_needs` used to always call its own `mvp.needs` agent and
invent every beat's requirement independently. Now:

1. If `has_detective_plan(db, graph.id)`, build `detective_raw_needs(db, graph)` — the detective's
   scenes/actions re-shaped into the **same raw payload** `validate_needs` already validates
   agent output against (no new trust boundary, no new validation path).
2. Run it through the existing `validate_needs` unchanged.
3. Beats the detective plan left with no surviving requirement are gap-filled by the `mvp.needs`
   agent call, exactly as before. If the detective plan already covers every beat, **the agent
   call is skipped entirely** (no cost, no new independent decision).
4. Merge: detective-covered beats win; agent-covered beats fill the rest; entities merge
   (detective's own `RevealNode`-derived entities plus whatever the agent introduced).

Absent a detective plan, `covered` is empty, the merge branch is never taken, and `plan_needs`
calls the agent exactly as it did before this change — proven by the full pre-existing test suite
(`tests/test_mvp_library.py`, `test_host.py`, `test_visual_production.py`, `test_mvp_config.py` —
98 tests, unchanged) still passing untouched.

## 4. The five obstacles — replaced, gated, or deferred

| Obstacle | File:line (Section A) | Status this pass |
| --- | --- | --- |
| 1. Photos/maps before `fill_beat` | `direct.py:_direct_pass` ~694-700 | **Replaced differently than planned**: rather than reordering the dense, cached `_direct_pass` per-beat loop (high regression risk, not attempted), a dispatchable detective graphic now gets its own carved moment at `compose()` time via `place_detective_graphics`, the same mechanism the detective board already uses to claim time regardless of what `_direct_pass` already filled. Proven for `fact_card`/`floor_plan`. |
| 2. "Most beats get none" engine cap | `engine_plan_system.md` lines 1, 52 | **Satisfied by construction, not yet exercised**: a beat whose graphic comes through direct dispatch never goes through `mvp.engine_plan`'s agent call at all, so the cap cannot apply to it. Not exercised end-to-end because no engine-kind tool (`courtroom`, `custody`, etc.) has a data builder in `dispatch.BUILDERS` yet — see "Not done". |
| 3. `check_item` unknown needs a question/disputed row | `engines.py:112-130` | **Replaced**, tested (`test_obstacle_3_...`). `legacy_evidence_additions` re-shapes `DetectiveQuestion`/`DetectiveClaim` rows into the exact `category="question"`/`disputed` ref shape `check_item` already accepts — zero change to `check_item` itself. |
| 4. `used_items`/`graphic_checks` block all repeats | `alternatives.py:399`, `coverage.py:101-121` | **Replaced at the `coverage.py` layer**, tested (`test_obstacle_4_...`): a shot carrying `detective_reuse_reason` is exempt from the repeated-fact publication blocker. `alternatives.py:usable`'s selection-time block is unchanged (out of scope: it only matters inside the competitive offer-selection path, which directly-dispatched detective shots bypass entirely). |
| 6. Dated event always wins the board card | `detective_board.py:76-99` | **Replaced**, tested (`test_obstacle_6_...`): `select_cards` takes an optional `card_overrides: dict[beat_id, {kind, source_fact}]`, resolved from `detective_board`-kind tool actions via `dispatch.board_card_overrides`, wired into `app/agents/detective_board.py:context()`. Absent an override, the original priority is unchanged. |

Obstacles 5 (`beat_view` truncation) and 7 (cue-fraction graphics cut by later board insertion) are
explicitly Section F's and H's jobs respectively (narration/visual/attention sync; production
integration) — not attempted here, to avoid scope creep into sections whose own acceptance
criteria this would only partially satisfy.

## 5. T07 Timeline bug (owner-reported, unrelated to the detective plan)

`app/documentary/mvp/alternatives.py:timeline_offers` sorted events by `(beat, date)` — narration
order first, date only as a tiebreaker — so a chronologically-earlier event narrated later (a
flashback) rendered to the *right* of later events on the timeline's spatial line. Fixed to sort
by `(date, beat)`: the line now always grows left to right by actual date, narration order only
breaks ties between same-day events. Regression test:
`tests/test_mvp_alternatives.py::test_timeline_events_are_ordered_by_date_not_by_when_the_narration_tells_them`
(fixture reproduces the exact owner-reported shape: a 2024 event narrated after two 2026 events).

**Known remaining limitation, stated honestly**: this fix assumes a revealed event's position is
always the correct final slot once placed — it does not handle a later-narrated event needing to
be inserted *between* two already-drawn/animated nodes on a card that already continued from a
previous one (`shown` is a scalar prefix count, not a set of revealed IDs). That is a deeper
rendering-model change (reveal order vs. spatial order as genuinely independent dimensions) and is
out of scope for this pass; it did not occur in the owner's reported case and the regression test
only covers the reported shape, not every possible non-monotonic reveal.

## 6. T01-T20: what changed vs. what still stands on Section A's reachability

Section A already established 21 of 22 rows "reachable" and one (`floor_plan`) dead. This section
does not re-verify all 20 end-to-end through the new dispatch path — only two:

- **T09 fact_card**: now also reachable via direct dispatch (`test_fact_card_dispatches_and_carves_a_real_shot_T09`), in addition to its existing `alternatives.py` reachability.
- **T14 floor_plan**: fixed from dead to reachable, via a fixture (no case has real plan-document geometry, and the renderer rightly refuses to invent one — see `floorplan.py:can_render`). `test_floor_plan_dispatches_when_payload_has_real_plan_data_T14` proves the path with synthetic-but-valid geometry.
- **T07 timeline**: unrelated bug fixed (see §5); reachability itself was already proven in Section A.

The other 17 tools' Section A reachability is untouched and unaffected (nothing in this pass
changes their existing call paths); they are **not yet proven reachable through the new direct-
dispatch mechanism** specifically, since `dispatch.BUILDERS` has no data builder for them. Checkpoint
rows for those 17 stay at their Section A status, not re-marked VERIFIED under Section D's
stricter per-tool dispatch criterion — see `docs/detective_toolkit_checkpoint.json`.

## 7. Not done in this pass (honest remaining work)

- Direct-dispatch data builders for the other 17 tools (map, detective_board-as-a-tool, recap,
  unknowns, conflict, conflict_board, investigation_clock, key_facts, document, quote, headline,
  evidence_check, custody, forensic, interrogation, courtroom, sentencing) plus photo/host.
- Obstacle 1's fix only covers beats reached via direct dispatch (fact_card/floor_plan); it does
  not reorder `_direct_pass`'s own photo-then-graphic sequence for the other tool kinds.
- Obstacle 2 is satisfied by construction but not exercised by a real engine-kind dispatch test.
- A fuller typed-schema tool descriptor (structured input/output contracts, not prose strings).

## 8. Live evidence and test results

No live paid LLM call was needed for this section (deterministic wiring + renderer fixes). Tests:
`tests/test_detective_section_d.py` (9 new tests, all passing), plus the modified/added tests in
`tests/test_mvp_alternatives.py` (Timeline fix). Full suite before this section's changes: 2997
passed, 12 skipped, 41 deselected (Section C's baseline). Full suite after: see commit message for
the exact re-run count — no regressions; the one golden-fixture update
(`tests/golden/production_script_previous.json`, adding `"detective_graphics": []` for the new
always-present, empty-when-ungated `compose()` output key) is a one-line, minimal diff.
