# Section E — the continuing board and meaningful returns

Completion proof required: "Several board returns retain recognizable positions and identities. A
known fact supports a later connection, an open question changes state at the correct passage,
and duplicate checks allow these justified returns."

## Already true before this section (verified, not rebuilt)

- **E1, card identity/position**: `app/documentary/detective_board.py:board_layout` computes each
  card's pixel position as a pure deterministic hash of the card's own `id` — a card never moves
  once it exists, regardless of what else is added later. No new code needed; this requirement was
  already satisfied by the existing cork-board layout.
- **E2, heard vs. displayed**: `appearances()` already separates `new_card_ids` from
  `cumulative_card_ids` per board appearance. Section B's event-sourced knowledge log already keeps
  full `history` per claim/question/relation (`replay_state`'s `record["history"]`), so "retaining
  earlier states for playback" is satisfied by Section B's design, not new work here.
- **E3, cross-tool ID consistency**: Section D's `legacy_evidence_additions` already merges
  `DetectiveClaim`/`DetectiveQuestion` rows into `ev.facts`/`ev.questions` keyed by their own
  `claim_key`/`question_key` (`app/documentary/mvp/evidence.py`). Any detective-authored claim a
  board override names via `source_fact` is the *same string* the recap board would key its
  `fact_id` on. Proven with a characterization test, not new code.

## The real gap found and fixed

`_linked_cards` (in `detective_board.py`) unconditionally set every card's `"status"` to the
literal string `"new"`, discarding anything `card_overrides` already supplied. This meant a
question's displayed state could never actually change — even if the Detective Director returned
to the same question with a different state, the rendered card always said "new". This is the
concrete reason E2's "a question can move from open to ... answered" requirement was not yet true.

Fixed by:
1. `_linked_cards`: `"status": c.get("status", "new")` and a new passthrough `"reuse_reason":
   c.get("reuse_reason")`, instead of the hardcoded value. Default behavior (no override — every
   real case today) is unchanged: still `"new"`.
2. New module `app/documentary/detective/board_state.py`: `question_status_as_of(db, graph_id,
   question_key, story_order)` replays Section B's knowledge-event log up to the knowledge event
   anchored at or before the given narration position (via `release_anchors` -> `narration_units`),
   and returns the question's current `status` from that replay — never a later answer, since
   `replay_state` only reads events up to the given `order_seq`.
3. `app/documentary/detective/dispatch.py:board_card_overrides`: for a `detective_board` tool
   action of kind `open_question`, computes `status` via (2) using the action's own covered
   narration unit, and passes through the action's `reuse_reason` when the Director set one. Fully
   additive — only active for a `detective_board` tool action that exists (i.e. only when Section C
   actually ran for that beat); nothing changes for a blueprint without detective scenes.

## What was confirmed as already fine, not needing a fix

- `select_cards` has no cross-beat used-fact tracking at all — it only ever considered one
  candidate per *beat* (`by_fact_beat` keyed by `first_beat`). A `card_overrides` entry naming the
  same `source_fact` at a later beat was never blocked by anything in this function; reuse already
  works mechanically. Proven with
  `test_select_cards_allows_a_fact_to_be_deliberately_reused_at_a_later_beat`.
- Update history (requirement 4 in the brief): `record_knowledge_event`'s append-only design and
  `replay_state`'s full `history` list (Section B) already satisfy this; no board-specific change
  needed. Reconstructing "the board as of an earlier beat" for an owner-facing inspector view is
  explicitly Section I's job, not attempted here.

## Deferred (not this section's job)

- A real UI/visual treatment distinguishing a "return" card from a "new" card (dimming, a small
  badge, etc.) is Section G's (Visual Design Director) job — this section only makes the *data*
  (`status`, `reuse_reason`) available to render from.
- Extending the claim_key/question_key bridge to the detective board's *own* candidate-building
  (today `select_cards`'s own `by_fact_beat`/`opened_by_beat` still read blueprint-native
  `told_events`/`opens`/`answers`, not Section B's claims/questions, for the *non-overridden* path)
  is out of scope: the override path (Section D/E's actual seam) already carries Section B's keys
  end to end; the un-overridden legacy path is intentionally left alone per the gating discipline.

## Tests

`tests/test_detective_section_e.py` (5 new):
- `test_linked_cards_no_longer_overwrites_status_and_reuse_reason_with_new`
- `test_linked_cards_defaults_status_to_new_when_none_is_supplied` (regression guard on default path)
- `test_board_card_overrides_reflects_the_question_state_at_its_own_beat_not_the_latest`
- `test_select_cards_allows_a_fact_to_be_deliberately_reused_at_a_later_beat`
- `test_a_detective_claim_key_is_the_same_string_on_the_board_and_in_the_recap_pack`

No migration needed — all tables used already exist from migrations 0071/0072.
