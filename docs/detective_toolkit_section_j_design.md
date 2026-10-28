# Section J — Film Experience Reviewer and system verification

Closes `docs/TrueCrime_Detective_Planner_Implementation_Prompt.md` Section J, plus checklist rows
`H3`/`H4` (deferred by Section H pending this section's repair-routing logic).

## What was reused

- `app/documentary/production/critics.py`/`app/agents/critics.py` (`DocumentaryCritics`,
  `CRITIC_ROLES`, `deterministic_checks`, `apply_fixes`) is this repo's existing critic/review
  infrastructure, as the doc asks. The Film Experience Reviewer is a new agent role on the same
  `run_agent` calling convention — its new contribution is routing a finding to one of **four**
  different repair owners rather than one generic in-place shot fix, which the existing critics
  system doesn't do.
- Section I's `inspector.plan_summary`/`design_reviews_for` supply `detective_plan`/`design_reviews`
  directly — no new planning data invented.
- Section C's `director.all_narration` (promoted from a private `_all_narration` helper, with
  `_all_unit_keys` → `all_unit_keys` alongside it, so this module doesn't reach into another
  module's private helpers) supplies `final_narration`.
- Section C's `director.repair`, Section G's `design_review.review`, and Section D's
  `dispatch.resolve_dispatchable_actions` are the actual repair actions `apply_repair` dispatches
  to for `detective_director`/`visual_design_director`/`scheduler_renderer` respectively.

## What was built

- Prompt (verbatim): `prompts/documentary/detective/film_reviewer_system.md`.
- Agent registration: role `detective_film_reviewer` (tier `writer`) in `config/agents.json`,
  `app/core/ai_config.py`, `config/models.json`.
- `app/documentary/detective/review_schema.py`: validates the OUTPUT envelope, and specifically
  enforces **inspection-mode honesty** — `plan_audit` can never claim `motion`/`audio`; each mode
  has a maximum claimable `inspection_modes` set; `outcome: "pass"` cannot carry open findings.
- `app/documentary/detective/review.py`:
  - `build_input`/`review`: the real call path (`mode`: `plan_audit`/`rendered_review`/`repair_check`).
  - `persist_findings`: one `DetectiveFinding` row per reported finding (new migration `0076`,
    applied and round-tripped against live Postgres, confirmed via `information_schema` and
    `tests/test_migrations.py`'s schema-consistency test).
  - `apply_repair`: routes a finding to its owner. An underlying failure is caught and reported as
    `interrupted: True` with the finding left `repair_in_progress` (a plain resume, never a lost or
    falsely-closed finding, never a double charge).
  - `recheck_finding`: re-reviews in `repair_check` mode; closes the finding **only if** it's no
    longer reported. A regression found during that same recheck is persisted as its **own** new
    finding (never merged into or hidden by the one being closed).
  - `repair_and_recheck_until`: bounded repair/recheck loop (`max_attempts`); exhausting it sets
    `status="incomplete"` — never reported as a false pass (H4).
- `app/db/models.py`: `DetectiveFinding` (graph-scoped, `repair_owner`/`severity`/`status` check
  constraints, `repairs_finding_id` self-FK for a repair's own recheck lineage).

## Live evidence (scratch case 26, graph 8, never case 5)

Real `map_film` call (`agent_run_id=31720`: 3 entities, 2 claims, 1 question, 2 relations,
5 knowledge events, 2 scenes, 2 actions) followed by a real `plan_audit` review call
(`agent_run_id` captured in `provider_calls` as "Film Experience Reviewer: plan_audit"). Outcome:
`pass`, `inspection_modes: ["plan", "transcript_only"]` — correctly never claiming `motion`/`audio`
for a plan-only review. 0 findings on this clean fixture. Total real cost: **€0.03294033**
(`provider_calls` ids 77721–77722, confirmed by direct SQL), far under the €300 cap.

`rendered_review` mode needs real rendered media, which this scratch case doesn't have (no render
has run for it) — that mode is covered by the mocked-provider tests instead, and genuine
`rendered_review`/`repair_check`-against-real-media proof is explicitly Section K's job (a real
case's real render), not fabricated here.

## The complete repair loop (H4)

`tests/test_detective_film_reviewer.py` proves, with a real `DetectiveFinding` row and a mocked
provider/repair call (so the loop logic itself is exercised deterministically, not dependent on a
live model's actual behavior):

- **Verified repair closes the finding**: `test_full_repair_loop_closes_a_verified_repair`.
- **A repair that introduces a regression**: `test_a_repair_that_introduces_a_regression_opens_a_new_finding_without_closing_silently`
  — the original closes (it IS fixed); the new problem opens as its own finding.
- **An interrupted iteration**: `test_an_interrupted_repair_call_leaves_a_resumable_finding_not_a_false_close`.
- **Exhausted retry/budget**: `test_exhausted_retries_leave_an_incomplete_state_not_a_false_pass`.
- **Routing for all four owners**: `test_routing_covers_all_four_repair_owners`.

## The 16-item integration checklist (doc's own list under Section J)

| # | Item | Coverage |
|---|------|----------|
| 1 | Gun example | **Existing** — `tests/test_detective_memory.py::test_gun_example_partial_knowledge_then_later_answer` |
| 2 | No metadata prerequisite | **Existing** — `tests/test_detective_memory.py::test_claim_question_relation_need_no_fact_or_contradiction_row` |
| 3 | Not-yet-known vs. unresolved | **Existing** — `tests/test_detective_memory.py::test_replay_hides_future_answer_before_its_event`; kind/status enums kept separate throughout `app/documentary/memory.py` |
| 4 | Partial knowledge and conflict | **Existing** — `tests/test_detective_section_d.py::test_obstacle_3_a_detective_unknown_with_no_fact_row_passes_check_item` plus B3's basis/attribution fixtures |
| 5 | Within-beat timing | **Existing** — `tests/test_detective_section_f.py::test_a_late_qualification_delays_the_dispatched_shot_past_the_beat_start`; `tests/test_detective_director.py::test_schema_rejects_premature_release` |
| 6 | Complete text coverage | **Existing** — `tests/test_detective_memory.py::test_truncated_fixture_still_returns_complete_span` |
| 7 | Purposeful choice | **Partial** — `test_fact_card_dispatches_and_carves_a_real_shot_T09` + `test_dispatched_graphic_survives_finalize_graphics_H1` prove a graphic gets its own carved moment (obstacle 1 fixed); a true side-by-side "same beat, photo available, graphic still wins" competitive-choice test is D2's stated remaining gap, not fabricated here |
| 8 | Continuity | **Existing** — `tests/test_detective_section_e.py::test_select_cards_allows_a_fact_to_be_deliberately_reused_at_a_later_beat`, `test_board_card_overrides_reflects_the_question_state_at_its_own_beat_not_the_latest` |
| 9 | Composition integrity | **Existing** — `tests/test_detective_section_h.py::test_dispatched_graphic_survives_finalize_graphics_H1` |
| 10 | Language and audio | **Partial** — narration is language-tagged throughout (Section I); RTL/German-expansion UI verification remains I4's stated gap |
| 11 | Edits and recovery | **Existing** — `tests/test_detective_inspector.py::test_correcting_a_claim_invalidates_only_the_bound_scene`; `tests/test_detective_toolkit_boundary.py::test_toolkit_invoke_never_re_pays_for_an_existing_plan`; **new** — this section's interruption/exhausted-retry tests extend recovery to the repair loop itself |
| 12 | Capability preservation | **Existing** — `tests/test_detective_section_d.py::test_floor_plan_dispatches_when_payload_has_real_plan_data_T14` plus Section A's 21/22-reachable capability matrix |
| 13 | Design agent integration | **Existing** — `tests/test_detective_design_director.py::test_review_calls_the_agent_with_the_real_rendered_frame_and_persists_a_run` (live round-trip in Section G's own evidence) |
| 14 | Visual meaning and readability | **Existing** — `tests/test_detective_design_director.py::test_design_schema_rejects_quotation_rewrite`, `test_design_schema_allows_faithful_shortening_of_a_non_exact_label`; full RTL/German-expansion fixtures remain G3's stated gap |
| 15 | Aim before tool | **Partial** — every persisted scene carries `why_selected`/`expected_audience_gain` (`tests/test_detective_director.py::test_map_film_calls_agent_validates_and_persists`); a true "same assets, different narrated aim → different tool" comparative fixture doesn't exist yet — stated gap |
| 16 | Reviewer honesty | **New, this section** — `tests/test_detective_film_reviewer.py::test_schema_rejects_plan_audit_claiming_motion_or_audio` and the full repair-loop test set above |

12/16 fully covered by existing or this section's new tests; 4 (7, 10, 14, 15) partially covered
with the remaining gap named plainly rather than claimed complete.

## Checkpoint

`H3`, `H4`, `J1`, `J2`, `J4` → `VERIFIED`. `J3` → `IN_PROGRESS` (plan_audit's honesty is proven
live; `rendered_review`'s honest-scope behavior against real media needs Section K's real case).
