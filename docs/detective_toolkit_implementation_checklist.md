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
| [ ] A1 | Current instructions, implementation paths, saved-project baseline, and all relevant prompts/configuration are mapped. | NOT_STARTED |
| [ ] A2 | Every registered visual tool is traced to its renderer, inputs, production caller, and owner control. | NOT_STARTED |
| [ ] A3 | Conflicting old selection/validation rules are identified and replaced where required without disabling useful capabilities. | NOT_STARTED |

## B. Investigation memory

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] B1 | Final narration is the content reference; valid text spans work without research rows. | NOT_STARTED |
| [ ] B2 | Stable entities, claims, questions, relationships, anchors, replayable knowledge events exist. | NOT_STARTED |
| [ ] B3 | Partial knowledge / attributed / disputed / not-yet-explained / explicit-unknown stay distinct. | NOT_STARTED |
| [ ] B4 | Details survive long passages; first introduction separate from later reference. | NOT_STARTED |

## C. Detective Director

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] C1 | Complete prompt, input wrapper, schema, model config, and real call path connected. | NOT_STARTED |
| [ ] C2 | Every consequential selection follows an investigative aim with recorded expected benefit. | NOT_STARTED |
| [ ] C3 | Question payoffs, connections, human interest, breathing room follow the text. | NOT_STARTED |
| [ ] C4 | Future knowledge does not leak; no unsupported certainty/conclusions. | NOT_STARTED |

## D. Twenty tools as executable detective tools

| Check | What must be true | Status |
| --- | --- | --- |
| [ ] D1 | All twenty tools have usable adapters (see T01–T20 below). | NOT_STARTED |
| [ ] D2 | Tools/photos/clips/host shots selected by purpose, not old arbitrary rules. | NOT_STARTED |
| [ ] D3 | Typed payloads, release bindings, availability outcomes, state updates reach execution. | NOT_STARTED |
| [ ] D4 | Fallback preserves intent; unsupported treatment reported honestly. | NOT_STARTED |

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
| [ ] T01 | Map / zoom map | NOT_STARTED |
| [ ] T02 | Detective board | NOT_STARTED |
| [ ] T03 | Fact / recap board | NOT_STARTED |
| [ ] T04 | Known / unknown panel | NOT_STARTED |
| [ ] T05 | Conflict comparison | NOT_STARTED |
| [ ] T06 | Conflict board | NOT_STARTED |
| [ ] T07 | Timeline | NOT_STARTED |
| [ ] T08 | Investigation clock | NOT_STARTED |
| [ ] T09 | Fact card | NOT_STARTED |
| [ ] T10 | Key facts card | NOT_STARTED |
| [ ] T11 | Document highlight | NOT_STARTED |
| [ ] T12 | Quotation card | NOT_STARTED |
| [ ] T13 | Newspaper headline | NOT_STARTED |
| [ ] T14 | Floor plan | NOT_STARTED |
| [ ] T15 | Evidence check | NOT_STARTED |
| [ ] T16 | Chain of custody | NOT_STARTED |
| [ ] T17 | Forensic examination | NOT_STARTED |
| [ ] T18 | Interview analysis | NOT_STARTED |
| [ ] T19 | Courtroom reasoning | NOT_STARTED |
| [ ] T20 | Sentencing explanation | NOT_STARTED |

## Final sign-off

- [ ] All A–K requirements and all three runtime prompts checked against the actual implementation.
- [ ] Every required atomic row is VERIFIED with current evidence; none hidden as failed/blocked/unchecked/stale.
- [ ] All twenty tools have individual proof; the detective can select/combine them by aim.
- [ ] Final integrated film behavior matches text, knowledge progression, chosen explanations.
- [ ] Design corrections rendered and rechecked; final encoded output still meets readability checks.
- [ ] Tests vs. actual viewing/listening observations distinguished; uninspected conditions stated.
- [ ] Any defect found during final audit routed back, repaired, rechecked.
- [ ] Owner received checklist, evidence, final prompts/config, previews, truthful report.
