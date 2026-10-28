# Detective toolkit implementation checklist
Tracks implementation of `docs/TrueCrime_Detective_Planner_Implementation_Prompt.md` (sections A-K,
including the owner's 2026-10-28 architecture correction: the detective toolkit is an internal
service the Main Visual Planner calls through one observable, persisted boundary — not a parallel
planner — see the master doc's "Architecture rule" section and rows A4/C5/D5/H5 below).
This file is a human-readable summary. The authoritative, per-row record (implementation location,
validation method, actual result, evidence paths, versions, dependencies, repair history, remaining
issue) lives in `docs/detective_toolkit_checkpoint.json` — update both together.

Baseline: commit `646bbeea9a2e2639fac8e679bb3308ded0a80fe5` on `main`.
Branch: `feature/detective-toolkit-planner`.

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `FAILED`, `BLOCKED`, `NEEDS_RECHECK`, `VERIFIED`.
A row is checked `[x]` only when its checkpoint entry is `VERIFIED` with current evidence.

## A. Audit

| Check | What must be true | Status |
| --- | --- | --- |
| [x] A1 | Current instructions, implementation paths, saved-project baseline, and all relevant prompts/configuration are mapped. | VERIFIED — docs/detective_toolkit_capability_matrix.md |
| [x] A2 | Every registered visual tool is traced to its renderer, inputs, production caller, and owner control. | VERIFIED — docs/detective_toolkit_capability_matrix.md |
| [x] A3 | Conflicting old selection/validation rules are identified and replaced where required without disabling useful capabilities. | VERIFIED — docs/detective_toolkit_section_d_design.md |
| [x] A4 | The detective toolkit is registered as a callable internal tool of the Main Visual Planner, with one observable, persisted call boundary and no competing top-level pipeline. | VERIFIED — docs/detective_toolkit_section_cd_architecture_remediation.md |

## B. Investigation memory

| Check | What must be true | Status |
| --- | --- | --- |
| [x] B1 | Final language-specific narration is the content reference; valid text spans work without research rows. | VERIFIED — tests/test_detective_memory.py::test_claim_question_relation_need_no_fact_or_contradiction_row |
| [x] B2 | Stable entities, claims, questions, relationships, anchors, and replayable knowledge events exist. | VERIFIED — tests/test_detective_memory.py::test_replay_hides_future_answer_before_its_event |
| [x] B3 | Partial knowledge, attributed/disputed statements, not-yet-explained details, and explicit unknowns remain distinct. | VERIFIED — tests/test_detective_memory.py::test_gun_example_partial_knowledge_then_later_answer |
| [x] B4 | Important details survive long passages and retrieval; first introduction is separate from later reference. | VERIFIED — tests/test_detective_memory.py::test_truncated_fixture_still_returns_complete_span |

## C. Detective Director

| Check | What must be true | Status |
| --- | --- | --- |
| [x] C1 | The complete prompt, input wrapper, schema, model/agent configuration, and real call path are connected. | VERIFIED — docs/detective_toolkit_section_c_design.md |
| [x] C2 | Every consequential tool selection follows an investigative aim and records its expected audience benefit. | VERIFIED — docs/detective_toolkit_section_c_design.md |
| [ ] C3 | Question payoffs, meaningful connections, human interest, and breathing room follow the text. | IN_PROGRESS — docs/detective_toolkit_section_c_design.md |
| [x] C4 | Future knowledge does not leak; unsupported certainty, conclusions, or connections are not added. | VERIFIED — tests/test_detective_director.py::test_map_film_rejects_premature_release_without_persisting |
| [x] C5 | The Detective Director runs only through the Main Visual Planner's detective-tool boundary; planner ownership of the final scene graph is preserved. | VERIFIED — docs/detective_toolkit_section_cd_architecture_remediation.md |

## D. Twenty tools as executable detective tools

| Check | What must be true | Status |
| --- | --- | --- |
| [x] D1 | All twenty tools have usable adapters or completed wiring to existing renderers. | VERIFIED — app/documentary/detective/dispatch.py |
| [ ] D2 | Tools, photos, clips, and host shots can be selected or combined by purpose without old arbitrary selection rules. | IN_PROGRESS — tests/test_detective_section_d.py |
| [x] D3 | Typed payloads, release bindings, availability outcomes, and state updates reach execution. | VERIFIED — tests/test_detective_section_d.py::test_fact_card_dispatches_and_carves_a_real_shot_T09 |
| [x] D4 | A fallback preserves the intended explanation; an unsupported treatment is reported honestly. | VERIFIED — tests/test_detective_section_d.py::test_unsupported_tool_id_is_reported_not_silently_dropped |
| [x] D5 | Every tool action has a typed toolkit result that the Main Visual Planner can accept, combine, defer, reject, or route for repair without an unrecorded downstream override. | VERIFIED — docs/detective_toolkit_section_cd_architecture_remediation.md |

## E. Continuing board and meaningful returns

| Check | What must be true | Status |
| --- | --- | --- |
| [x] E1 | Board returns preserve identities, recognizable positions, relationships, and historical state. | VERIFIED — docs/detective_toolkit_section_e_design.md |
| [x] E2 | Heard knowledge and displayed board state are separate; additions and status changes appear deliberately. | VERIFIED — tests/test_detective_section_e.py::test_board_card_overrides_reflects_the_question_state_at_its_own_beat_not_the_latest |
| [x] E3 | A fact can return for a new connection, comparison, or answer without duplicate-first-reveal errors. | VERIFIED — tests/test_detective_section_e.py::test_select_cards_allows_a_fact_to_be_deliberately_reused_at_a_later_beat |

## F. Narration/visual/attention sync

| Check | What must be true | Status |
| --- | --- | --- |
| [x] F1 | Claims, captions, links, and conclusions appear at the correct sentence/clause, including late qualifications. | VERIFIED — docs/detective_toolkit_section_f_design.md |
| [ ] F2 | Reading time, motion, emphasis, pauses, and speech fit together without unrelated competing demands. | IN_PROGRESS — docs/detective_toolkit_section_f_design.md |
| [ ] F3 | The narration remains understandable without looking; essential repairs route through the text workflow. | IN_PROGRESS — docs/detective_toolkit_section_f_design.md |

## G. Alternative Visual Design Director

| Check | What must be true | Status |
| --- | --- | --- |
| [x] G1 | The full prompt and design/review/repair modes execute with actual renderer output. | VERIFIED — docs/detective_toolkit_section_g_design.md |
| [ ] G2 | Layout, hierarchy, fonts, spacing, contrast, wrapping, and connectors are visibly improved. | IN_PROGRESS — docs/detective_toolkit_section_g_design.md |
| [ ] G3 | First, reveal, dense, final, transition, and smaller-view conditions are inspected. | IN_PROGRESS — docs/detective_toolkit_section_g_design.md |
| [x] G4 | Repairs preserve exact quotations, claim meaning, uncertainty, attribution, relationships, and anchors. | VERIFIED — tests/test_detective_design_director.py::test_review_rejects_output_that_rewrites_a_bound_quote |
| [x] G5 | A proposed repair is re-rendered and inspected before pass; missing images do not yield visual approval. | VERIFIED — docs/detective_toolkit_section_g_design.md |

## H. Production integration

| Check | What must be true | Status |
| --- | --- | --- |
| [x] H1 | Detective intent and chosen tools survive composition, coverage filling, host insertion, and final encoding. | VERIFIED — tests/test_detective_section_h.py::test_dispatched_graphic_survives_finalize_graphics_H1 |
| [ ] H2 | All three agents and deterministic components share current versions, state, and timing. | IN_PROGRESS — tests/test_detective_section_h.py::test_no_hidden_staleness_because_nothing_is_cached_yet_H2 |
| [x] H3 | Findings route to the correct owner, produce a repair, and close only after the new output passes. | VERIFIED — tests/test_detective_film_reviewer.py::test_full_repair_loop_closes_a_verified_repair |
| [x] H4 | New regressions reopen checks; exhausted settings leave a resumable incomplete state rather than approval. | VERIFIED — tests/test_detective_film_reviewer.py::test_exhausted_retries_leave_an_incomplete_state_not_a_false_pass |
| [x] H5 | The final production script and renderer consume the Main Visual Planner's accepted detective-tool decisions, with no bypass from toolkit or coverage helpers. | VERIFIED — tests/test_detective_section_h.py::test_unaccepted_action_is_never_dispatched_H5 |

## I. Inspection, targeted revision, reruns

| Check | What must be true | Status |
| --- | --- | --- |
| [x] I1 | The inspector exposes aims, text, state changes, tool reasons, previews, design findings, and loop progress. | VERIFIED — tests/test_detective_inspector.py |
| [x] I2 | Edits invalidate affected dependencies and stale approvals while preserving unrelated valid work. | VERIFIED — tests/test_detective_inspector.py::test_correcting_a_claim_invalidates_only_the_bound_scene |
| [x] I3 | Owner edits, saved projects, checkpoints, and interrupted work remain usable. | VERIFIED — tests/test_detective_toolkit_boundary.py::test_toolkit_invoke_never_re_pays_for_an_existing_plan |
| [ ] I4 | English, German, Persian, and Arabic use their actual text/timing, fonts, and readable layouts. | IN_PROGRESS — docs/detective_toolkit_section_i_design.md |

## J. Film Experience Reviewer + system verification

| Check | What must be true | Status |
| --- | --- | --- |
| [x] J1 | The complete prompt and plan/render/repair-check modes are active with structured repair routing. | VERIFIED — docs/detective_toolkit_section_j_design.md |
| [x] J2 | The reviewer checks achieved audience understanding, continuity, curiosity, meaningful reuse, and audio fluency. | VERIFIED — docs/detective_toolkit_section_j_design.md |
| [ ] J3 | Frame-only, transcript-only, excerpt, and unavailable-media limits are reported accurately. | IN_PROGRESS — tests/test_detective_film_reviewer.py::test_schema_rejects_plan_audit_claiming_motion_or_audio |
| [x] J4 | Meaningful targeted and integrated tests pass, including repairs and stale-review invalidation. | VERIFIED — docs/detective_toolkit_section_j_design.md |

## K. Real-film proof and handover

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] K1 | The same real passage is compared before/after, showing purposeful tool use and continuing boards. | NOT_STARTED |
| [ ] K2 | Final encoded output is checked in the stated viewing/listening modes; fixture proof is distinguished. | NOT_STARTED |
| [ ] K3 | All prompts, schemas/configuration, proofs, test results, and review outcomes are supplied. | NOT_STARTED |
| [ ] K4 | Every required atomic entry is verified against current versions; any failed/stale entry has re-entered the repair loop. | NOT_STARTED |

## Individual toolkit (T01-T20)

| Check | Tool | Status |
| --- | --- | --- |
| [ ] T01 | Map / zoom map | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T02 | Detective board | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T03 | Fact / recap board | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T04 | Known / unknown panel | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T05 | Conflict comparison | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T06 | Conflict board | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [x] T07 | Timeline | VERIFIED — tests/test_mvp_alternatives.py::test_timeline_events_are_ordered_by_date_not_by_when_the_narration_tells_them |
| [ ] T08 | Investigation clock | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [x] T09 | Fact card | VERIFIED — tests/test_detective_section_d.py |
| [ ] T10 | Key facts card | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T11 | Document highlight | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T12 | Quotation card | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T13 | Newspaper headline | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [x] T14 | Floor plan | VERIFIED — tests/test_detective_section_d.py |
| [ ] T15 | Evidence check | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T16 | Chain of custody | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T17 | Forensic examination | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T18 | Interview analysis | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T19 | Courtroom reasoning | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |
| [ ] T20 | Sentencing explanation | IN_PROGRESS — docs/detective_toolkit_capability_matrix.md |

## Final sign-off

- [ ] All A-K requirements and all three runtime prompts checked against the actual implementation.
- [ ] Every required atomic row is VERIFIED with current evidence; none hidden as failed/blocked/unchecked/stale.
- [ ] All twenty tools have individual proof; the detective can select/combine them by aim.
- [ ] Final integrated film behavior matches text, knowledge progression, chosen explanations.
- [ ] Design corrections rendered and rechecked; final encoded output still meets readability checks.
- [ ] Tests vs. actual viewing/listening observations distinguished; uninspected conditions stated.
- [ ] Any defect found during final audit routed back, repaired, rechecked.
- [ ] Owner received checklist, evidence, final prompts/config, previews, truthful report.
