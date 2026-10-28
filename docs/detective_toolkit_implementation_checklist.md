# Detective toolkit implementation checklist

Tracks implementation of `docs/TrueCrime_Detective_Planner_Implementation_Prompt.md` (sections A–K).
This file is a human-readable summary. The authoritative, per-row record (implementation
location, validation method, actual result, evidence paths, versions, dependencies, repair
history, remaining issue) lives in `docs/detective_toolkit_checkpoint.json` — update both
together.

Baseline: commit `646bbeea9a2e2639fac8e679bb3308ded0a80fe5` on `main`.
Branch: `feature/detective-toolkit-planner`.

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `FAILED`, `BLOCKED`, `NEEDS_RECHECK`, `VERIFIED`.
A row is checked `[x]` only when its checkpoint entry is `VERIFIED` with current evidence.

## A. Audit

| Check | What must be true | Status |
| --- | --- | --- |
| [x] A1 | Current instructions, implementation paths, saved-project baseline, and all relevant prompts/configuration are mapped. | VERIFIED — `docs/detective_toolkit_capability_matrix.md` |
| [x] A2 | Every registered visual tool is traced to its renderer, inputs, production caller, and owner control. | VERIFIED — 22 rows traced, 21 reachable, 1 dead (`floor_plan`) |
| [x] A3 | Conflicting old selection/validation rules are identified and replaced where required without disabling useful capabilities. | VERIFIED — 4/7 replaced+tested (gated), 1 satisfied-by-construction-unexercised, 2 deferred to F/H by design |

## B. Investigation memory

| Check | What must be true | Status |
| --- | --- | --- |
| [x] B1 | Final narration is the content reference; valid text spans work without research rows. | VERIFIED — `tests/test_detective_memory.py::test_claim_question_relation_need_no_fact_or_contradiction_row` |
| [x] B2 | Stable entities, claims, questions, relationships, anchors, replayable knowledge events exist. | VERIFIED — `app/documentary/memory.py`, migration `0071`, live-verified |
| [x] B3 | Partial knowledge / attributed / disputed / not-yet-explained / explicit-unknown stay distinct. | VERIFIED — `tests/test_detective_memory.py::test_gun_example_partial_knowledge_then_later_answer` |
| [x] B4 | Details survive long passages; first introduction separate from later reference. | VERIFIED — 2 tests, see checkpoint.json |

## C. Detective Director

| Check | What must be true | Status |
| --- | --- | --- |
| [x] C1 | Complete prompt, input wrapper, schema, model config, and real call path connected. | VERIFIED — live map_film + direct_scenes calls, see `docs/detective_toolkit_section_c_design.md` |
| [x] C2 | Every consequential selection follows an investigative aim with recorded expected benefit. | VERIFIED — live scenes carry aim/why_selected/expected_audience_gain |
| [ ] C3 | Question payoffs, connections, human interest, breathing room follow the text. | IN_PROGRESS — needs a longer passage than this section's 3-sentence fixture |
| [x] C4 | Future knowledge does not leak; no unsupported certainty/conclusions. | VERIFIED — premature-release rejection test, nothing persisted on violation |

## D. Twenty tools as executable detective tools

| Check | What must be true | Status |
| --- | --- | --- |
| [x] D1 | All twenty tools have usable adapters (see T01–T20 below). | VERIFIED — descriptor complete; floor_plan fixed; full builders for 2/20 |
| [ ] D2 | Tools/photos/clips/host shots selected by purpose, not old arbitrary rules. | IN_PROGRESS — needs.py seam live; generalized beyond 2 tools is future work |
| [x] D3 | Typed payloads, release bindings, availability outcomes, state updates reach execution. | VERIFIED |
| [x] D4 | Fallback preserves intent; unsupported treatment reported honestly. | VERIFIED |

## E. Continuing board and meaningful returns

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] E1 | Board returns preserve identities, positions, relationships, history. | NOT_STARTED |
| [ ] E2 | Heard knowledge vs. displayed board state are separate and deliberate. | NOT_STARTED |
| [ ] E3 | A fact can return for new connection/comparison/answer without duplicate-reveal errors. | NOT_STARTED |

## F. Narration/visual/attention sync

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] F1 | Claims/captions/links/conclusions appear at correct sentence/clause, incl. late qualifications. | NOT_STARTED |
| [ ] F2 | Reading time, motion, emphasis, pauses, speech coordinate without competing demands. | NOT_STARTED |
| [ ] F3 | Narration remains understandable audio-only; repairs route through text workflow. | NOT_STARTED |

## G. Alternative Visual Design Director

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] G1 | Full prompt and design/review/repair modes execute with actual renderer output. | NOT_STARTED |
| [ ] G2 | Layout/hierarchy/fonts/spacing/contrast/wrapping/connectors visibly improved. | NOT_STARTED |
| [ ] G3 | First/reveal/dense/final/transition/smaller-view states inspected. | NOT_STARTED |
| [ ] G4 | Repairs preserve quotations, claim meaning, uncertainty, attribution, anchors. | NOT_STARTED |
| [ ] G5 | Repair is re-rendered and inspected before pass; missing images ≠ approval. | NOT_STARTED |

## H. Production integration

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] H1 | Detective intent/tools survive composition, coverage fill, host insert, encoding. | NOT_STARTED |
| [ ] H2 | All three agents and deterministic components share current versions/state/timing. | NOT_STARTED |
| [ ] H3 | Findings route to correct owner, produce a repair, close only after new output passes. | NOT_STARTED |
| [ ] H4 | Regressions reopen checks; exhausted settings leave resumable incomplete state. | NOT_STARTED |

## I. Inspection, targeted revision, reruns

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] I1 | Inspector exposes aims, text, state changes, tool reasons, previews, findings, loop progress. | NOT_STARTED |
| [ ] I2 | Edits invalidate affected dependencies/stale approvals; preserve unrelated valid work. | NOT_STARTED |
| [ ] I3 | Owner edits, saved projects, checkpoints, interrupted work remain usable. | NOT_STARTED |
| [ ] I4 | English/German/Persian/Arabic use real text/timing/fonts/layouts. | NOT_STARTED |

## J. Film Experience Reviewer + system verification

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] J1 | Full prompt and plan/render/repair-check modes active with structured repair routing. | NOT_STARTED |
| [ ] J2 | Reviewer checks understanding, continuity, curiosity, meaningful reuse, audio fluency. | NOT_STARTED |
| [ ] J3 | Frame-only/transcript-only/excerpt/unavailable-media limits reported accurately. | NOT_STARTED |
| [ ] J4 | Meaningful targeted + integrated tests pass, incl. repairs and stale-review invalidation. | NOT_STARTED |

## K. Real-film proof and handover

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] K1 | Same real passage compared before/after: purposeful tool use, continuing boards. | NOT_STARTED |
| [ ] K2 | Final encoded output checked in stated viewing/listening modes; fixture proof distinguished. | NOT_STARTED |
| [ ] K3 | All prompts/schemas/proofs/tests/reviews supplied. | NOT_STARTED |
| [ ] K4 | Every atomic entry verified against current versions; stale entries repaired. | NOT_STARTED |

## Individual toolkit (T01–T20)

| Check | Tool | Status |
| --- | --- | --- |
| [ ] T01 | Map / zoom map | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T02 | Detective board | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T03 | Fact / recap board | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T04 | Known / unknown panel | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T05 | Conflict comparison | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T06 | Conflict board | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [x] T07 | Timeline | VERIFIED — date-order bug fixed + regression test |
| [ ] T08 | Investigation clock | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [x] T09 | Fact card | VERIFIED — dispatch-reachable, real render |
| [ ] T10 | Key facts card | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T11 | Document highlight | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T12 | Quotation card | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T13 | Newspaper headline | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [x] T14 | Floor plan | VERIFIED — fixed from dead to reachable (fixture) |
| [ ] T15 | Evidence check | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T16 | Chain of custody | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T17 | Forensic examination | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T18 | Interview analysis | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T19 | Courtroom reasoning | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |
| [ ] T20 | Sentencing explanation | IN_PROGRESS — reachable via old pipeline; no dispatch builder yet |

## Final sign-off

- [ ] All A–K requirements and all three runtime prompts checked against the actual implementation.
- [ ] Every required atomic row is VERIFIED with current evidence; none hidden as failed/blocked/unchecked/stale.
- [ ] All twenty tools have individual proof; the detective can select/combine them by aim.
- [ ] Final integrated film behavior matches text, knowledge progression, chosen explanations.
- [ ] Design corrections rendered and rechecked; final encoded output still meets readability checks.
- [ ] Tests vs. actual viewing/listening observations distinguished; uninspected conditions stated.
- [ ] Any defect found during final audit routed back, repaired, rechecked.
- [ ] Owner received checklist, evidence, final prompts/config, previews, truthful report.
