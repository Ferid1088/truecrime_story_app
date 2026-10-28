# Section C — the Detective Director agent

Implements `docs/TrueCrime_Detective_Planner_Implementation_Prompt.md` Section C on top of
Section A's capability matrix and Section B's investigation memory (`app/documentary/memory.py`).

## What was built

- **Prompt**: `prompts/documentary/detective/director_system.md` — the doc's Section C system
  prompt, copied verbatim.
- **Config**: `detective.director` registered in `config/agents.json`; `detective_director` role
  added to `app/core/ai_config.py` and `config/models.json` (tier `writer`, temperature 0.4,
  max_tokens 32000), following the `mvp_needs_planner` pattern exactly.
- **Tool catalog** (`app/documentary/detective/tool_catalog.py`): provisional, built from Section
  A's 22-row capability matrix with Section D's own investigative-job wording — explicitly marked
  superseded once Section D's real adapter layer lands.
- **Output validation** (`app/documentary/detective/schema.py`): checks required fields, the
  `basis`/`kind`/`subject_type` enums, unknown `tool_id`s, and — the semantic core of this section —
  that every content binding's release anchor is not positioned after the scene that cites it
  ("no future answers"). Violations are returned as a list, not persisted; the caller raises
  `DirectorValidationFailed` and persists nothing.
- **Persistence** (`app/documentary/detective/persist.py`): writes entities/claims/questions/
  relations/knowledge_events through Section B's own memory functions (so Section B's
  first-introduction/basis/kind invariants apply automatically), and scenes/actions into the two
  new tables below. A scene is **upserted** by `scene_key` — see the live-call finding below for
  why that matters.
- **New tables** (migration `0072_detective_scenes_and_actions.py`, applied and round-tripped live):
  `detective_scenes`, `detective_tool_actions`.
- **Call path** (`app/documentary/detective/director.py`): `map_film`, `direct_scenes`, `repair`,
  each building the doc's exact INPUT envelope (narration from Section B, `existing_state` from
  `replay_state` + stored scenes, the provisional tool catalog, a `schema_hints` block — see below)
  and calling `run_agent("detective.director", ...)`.

## Why a `schema_hints` block was added to the input (not in the doc, but necessary)

The doc's OUTPUT section describes fields in prose ("claims: stable ID, entity IDs, attribute,
short text, basis, ..."). A real model call (see live evidence below) initially used plausible but
different key names (`short_text` instead of `text`, `short_label` instead of `label`) and
non-canonical enum values (`basis: "documentary_record"`, `kind: "identity"`) — schema-shaped JSON
that still violated the contract. Rather than silently coercing those (which risks misrepresenting
an epistemic distinction the doc is explicit about preserving), the fix was to state the exact field
names and the exact canonical enum values in the input payload's `schema_hints`, and to make the
validator reject — not guess at — anything that still doesn't match. This is what Section C's own
text means by "Validate returned payloads before they reach renderers": validation is not
decorative, and a real call proved it catches real defects.

## Live evidence (scratch case, not a production case)

Real calls against the real configured provider (`apimaster`, model `gemini-3.8-flash`) — this
repo's test suite always runs under `TRUECRIME_TEST_NO_REAL_PROVIDERS=1`
(`tests/conftest.py`), so this was run as a standalone script, never under pytest.

- **Scratch case**: id `20`, slug `detective-director-live-5158c28ef0` (created solely for this
  proof; three earlier debugging attempts — cases 17/18/19 — were deleted via
  `app.services.case_control.delete_case_rows` once superseded, keeping their `provider_calls`
  ledger rows as required). Never case 5 or another real case.
- **Fixture**: 3 narration units across 2 beats (a gun found hidden in a drawer, ballistics
  confirming it had been fired with an unclear owner, and a pawn-shop receipt tracing it to a sale
  two years earlier) — representative, not a full real film; the real-case proof is Section K's job.
- **`map_film`**: `AgentRun` id `31711`, `ProviderCall` id `77710`, 3628 input / 13500 output tokens,
  **cost €0.04768065** (`cost_source=computed_from_tokens`). Produced and persisted 8 entities, 9
  claims (bases: `established_in_text`, `explicit_unknown`), 2 questions (kinds:
  `explicit_unresolved`, `not_yet_explained`), 3 relations, 11 knowledge events, and 3 scenes.
- **`direct_scenes`**: `AgentRun` id `31712`, `ProviderCall` id `77711`, 4643 input / 7571 output
  tokens, **cost €0.02848853**. Requested the same 3 narration units; it correctly reused
  `map_film`'s own scene IDs (`scn_01_discovery`, `scn_02_ballistics_and_ownership`,
  `scn_03_pawn_trail`) from `existing_state.previous_scenes` rather than inventing new ones — real
  evidence the memory hand-off works as intended.
- **Total live spend across this section's work (all attempts, including 3 debugging iterations
  that hit validation bugs before persisting anything): €0.19375662** — recorded actual spend,
  nowhere near the €300/case cap, and well within normal development cost.
- **Finding caught by this live run, fixed before considering the section done**: the first
  version of `persist_director_output` skipped a scene whose `scene_key` already existed, so
  `direct_scenes`'s real, more detailed actions were silently discarded in favor of `map_film`'s
  bare scene stub. Fixed to upsert (replace fields + replace ordered actions) instead, with a
  regression test (`tests/test_detective_director.py::test_persist_updates_an_existing_scene_instead_of_silently_skipping_it`).
- **Verify directly**:
  ```sql
  select id, agent_name, status, provider, model, input_tokens, output_tokens
    from agent_runs where id in (31711, 31712);
  select id, case_id, stage, model, tokens_in, tokens_out, cost_eur, cost_source
    from provider_calls where case_id = 20;
  select scene_key, mode, aim from detective_scenes where graph_id = 5 order by scene_order;
  select tool_id, purpose from detective_tool_actions
    where scene_id in (select id from detective_scenes where graph_id = 5);
  ```

## Tests (mocked provider — `tests/test_detective_director.py`, 8 tests, all passing)

Validation (premature release, unknown tool_id, missing fields, clean payload), persistence
(duplicate-first-reveal rejection reusing Section B's own invariant, the scene-upsert regression
above), and two end-to-end tests with a fake `generate_structured` standing in for the real
provider (`test_map_film_calls_agent_validates_and_persists`,
`test_map_film_rejects_premature_release_without_persisting` — the latter proves a validation
failure persists nothing at all).

## Honest scope / remaining issues (not blockers for C1-C4, but real)

- The tool catalog is Section A-derived and provisional; Section D replaces it with live
  availability per case.
- `asset_catalog` was empty for this proof (no photos/clips attached to the scratch case) — the
  director correctly chose text/graphic tools (`detective_board`, `forensic`, `unknowns`,
  `timeline`) rather than fabricating an asset-backed choice. Proving deliberate photo-vs-graphic
  choice with real assets available is Section D's completion proof, not this one's.
- `repair` mode has validation/persistence wiring identical to `map_film`/`direct_scenes` but has
  not itself been exercised by a live call in this section (map_film and direct_scenes were the two
  required by the doc's completion proof); a repair-loop live exercise is folded into Section H/J's
  injected-defect repair test per the doc's own test list.
- `state_before_order_seq`/`state_after_order_seq` on `DetectiveScene` stay null for now — the
  model currently supplies free-text `state_before`/`state_after` descriptions rather than
  resolvable order_seq pointers, and `persist.py` does not yet compute them from the replay log.
  Worth revisiting once Section F's real timing/anchors make the order_seq linkage load-bearing.

## Checkpoint status

`C1`-`C4` set in `docs/detective_toolkit_checkpoint.json`, dependencies `["A", "B"]`. See that file
for the full per-row evidence (implementation_location/validation_method/evidence/remaining_issue).
