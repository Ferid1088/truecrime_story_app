# Section B design — text-based investigation memory

Completion proof for checklist entries B1-B4. Implements the typed, versioned, stable-ID
contracts required by `docs/TrueCrime_Detective_Planner_Implementation_Prompt.md` Section B.

## Reuse decision: `EpistemicClaim`/`EpistemicContractSet` are NOT reused

`app/db/models.py` already has `EpistemicContractSet`/`EpistemicClaim` (span-anchored,
modality-typed claims, CRUD'd via `app/longform/api.py`). Investigated whether the real
documentary production pipeline calls them:

- `grep -rln "app.longform" app/ --include=*.py` outside `app/longform/`: only
  `app/main.py` (mounts the router) and `app/documentary/mvp/pipeline.py`.
- `grep -n "LF\." app/documentary/mvp/pipeline.py` shows pipeline.py calls **only**
  `LF.build_reveal_graph_payload` / `LF.create_reveal_graph` — the `RevealGraph` side of the
  longform service. Nothing in the real render pipeline ever touches
  `EpistemicContractSet`/`EpistemicClaim`.
- No frontend route or test outside `tests/test_longform_phase1.py` /
  `tests/test_longform_phase5.py` references `epistemic-contract`/`EpistemicClaim`.

**Finding:** `EpistemicContractSet`/`EpistemicClaim` is a real, tested, API-reachable feature
(editorial integrity review) but is **unintegrated with the real film pipeline** — the same
"registered but no production caller" pattern Section A found for `floor_plan`. It also encodes
a different concern (an editorial *review workflow* with `review_status`
proposed/approved/rejected) than what B needs (a *text-anchored investigation memory* with
basis/kind/state). Repurposing it would overload an existing, separately-tested feature's
meaning. **Decision: build new, dedicated tables for the detective toolkit**, and reuse
`RevealGraph`/`RevealNode` (which *is* live in the real pipeline) as the Entity backbone instead
of duplicating entity management.

## Schema

### Reused as-is
- `RevealGraph` (case/blueprint/version-scoped, already the real pipeline's graph).

### Extended
- `RevealNode` gains `aliases_json` (Text, default `"[]"`, additive/nullable-safe) — the doc's
  Entity contract requires aliases; `RevealNode` had name/category/first-reveal-anchor already.

### New tables (all FK to `reveal_graphs.id`, so every row is scoped to one versioned graph,
matching how `RevealNode` already works)

- `narration_units` — stable per-sentence narration spans, independent of any one claim, so
  multiple claims/relations/events can point to the same span by stable key.
- `release_anchors` — the words that establish a meaning within a narration unit; resolved
  timing (`resolved_start_seconds`/`resolved_end_seconds`) is nullable and filled later by
  Section F once real audio exists. The app creates these; agents only reference `anchor_key`.
- `detective_claims` — attribute-level claims with `basis` CHECK
  (`established_in_text`, `attributed`, `disputed`, `inference`, `explicit_unknown`), optional
  `attribution`, supporting narration-unit keys, release anchor, optional `evidence_fact_ids_json`
  link to pre-existing `Fact`/`Contradiction` rows (never required).
- `detective_questions` — `kind` CHECK (`not_yet_explained`, `explicit_unresolved`,
  `interpretive_question`) kept separate from current *state*, which lives in
  `detective_knowledge_events` (see below) so "not yet explained" vs. "explicitly unresolved"
  is never collapsed into one flag.
- `detective_relations` — typed/labeled edges (`relation_type` free-text per the doc's
  "typed, meaningful connections" — chronology/same_object/attributed_statement/
  explicit_conflict/supporting_detail/answer_to_question/...), distinct from `RevealEdge`
  (which stays a pure reveal-order DAG edge, untouched).
- `detective_knowledge_events` — the single generic event log for every state change
  (entity attribute learned, question open->partial->answered->reframed, relation
  established, claim revisited). `subject_type` + `subject_key` + `operation` +
  `prior_state_json`/`new_state_json` + `order_seq` (monotonic per graph, for deterministic
  replay) + `anchor_id`. One log, not a separate per-concept event table — the doc's own
  Knowledge Event contract is generic ("Anchor, subject, operation, prior/new state") and reused
  for entities, claims, questions and relations alike, which is also what keeps replay
  (B4/B2 proof) a single ordered scan instead of a multi-table merge.

## Live verification

Migration `0071` was applied with `alembic upgrade head` against the live, already-running
PostgreSQL database (same one `alembic current` reported at head `0070` before this work), then
round-tripped with `alembic downgrade -1` followed by `alembic upgrade head` again to prove the
downgrade path is real, not just written. Direct SQL against that live database afterward:

```
tables: ['detective_claims', 'detective_knowledge_events', 'detective_questions',
         'detective_relations', 'narration_units', 'release_anchors']
reveal_nodes.aliases_json: ('aliases_json', 'text', "'[]'::text")
check constraints: ['ck_detective_claim_basis', 'ck_detective_knowledge_event_subject_type',
                     'ck_detective_question_kind']
```

All six tables, the new column, and all three CHECK constraints exist permanently in the live
database at HEAD `0071` — this part of the proof does not depend on any test run.

**Test-data note:** `tests/test_detective_memory.py` uses this repo's existing `db_session`
fixture (`tests/conftest.py`), which runs each test against the same live PostgreSQL engine but
only `flush()`es within an open transaction and `close()`s without committing — this is this
suite's established convention (see `tests/test_longform_phase1.py`, same pattern), kept so test
runs never leave fixture rows in the real case database. Every INSERT/SELECT/constraint check in
the 5 tests below still executes against real Postgres (not SQLite, not a mock) — it is the
schema/constraints above, not the test rows, that persist permanently.

**Regression found and fixed:** a full-suite run (`pytest tests/ -q -x --ignore=tests/test_detective_memory.py -k "not golden"`, 1403 passed before the failure) caught
`tests/test_longform_phase2.py::test_case6_real_blueprint_builds_and_validates_persisted_graph`
failing with `sqlite3.OperationalError: no such column: reveal_nodes.aliases_json`. That test
reads a local, gitignored SQLite snapshot (`truecrime.db`, real case-6 data, not touched by the
Alembic migration above) through the same `RevealNode` ORM model, which now expects the new
column. Fixed by adding the same additive column directly to that file
(`ALTER TABLE reveal_nodes ADD COLUMN aliases_json TEXT DEFAULT '[]'`); re-ran that test alone
(passed) and the full suite again to confirm no other regression (see below).

Tests (all passing): `tests/test_detective_memory.py::test_gun_example_partial_knowledge_then_later_answer`,
`::test_claim_question_relation_need_no_fact_or_contradiction_row`,
`::test_truncated_fixture_still_returns_complete_span`,
`::test_replay_hides_future_answer_before_its_event`,
`::test_first_introduction_is_unique_but_reference_is_not` — `5 passed` (`pytest tests/test_detective_memory.py -q`).
