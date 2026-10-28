# Section F — synchronize narration, visuals, and audience attention

Dependencies: C, D, E (all VERIFIED). Closes Section A's obstacles 5 and 7 as far as this
section's own scope goes; real voice-take integration and full cross-tool scheduling remain for H.

## What already existed, reused as-is

- `app/documentary/voice_render.py`: `beat_times`/`sentence_times` already implement exactly the
  "word alignment where reliable, sentence-level timing where it is not" logic the doc asks for,
  against a real voice take's per-character alignment (`words_from_alignment`'s own input shape).
  `resolve_word_level` (new, `app/documentary/detective/timing.py`) is a thin wrapper reusing
  `beat_times` unchanged — no alignment math was duplicated.
- `app/documentary/detective_board.py:reading_hold_seconds`/`appearances` already scale a card's
  required reading time with its title's word count and only reveal a batch of cards once the gap
  until the next beat is long enough — real, word-count-aware reading-time gating for the
  detective board specifically. Characterized by
  `tests/test_detective_section_f.py::test_board_reading_time_already_scales_with_title_length_and_gates_appearance`,
  not rebuilt.
- Section B's `release_anchors` table already had `char_start`/`char_end` (offsets into their own
  `narration_unit.text`) and nullable `resolved_start_seconds`/`resolved_end_seconds` — exactly
  where real timing belongs once resolved. Confirmed nothing populated them before this section.

## What was built

1. **`app/documentary/detective/timing.py`** — two resolution tiers:
   - `resolve_word_level(unit_char_start_in_block, anchor_char_start, anchor_char_end, block_text, alignment, t0)`:
     real resolved seconds from a voice take's own character alignment, via `voice_render.beat_times`.
     Returns `(start, end, exact)`; `exact=False` when the alignment doesn't cleanly match the text
     (same detection `beat_times` already uses internally).
   - `anchor_fraction_within_span(anchor_char_start, anchor_char_end, unit_text_len, span_start, span_end)`:
     sentence/beat-level fallback — proportional position of the anchor within a *currently
     compiled* beat span. This is what `dispatch.py` actually uses at carve time, since the
     dispatcher only ever sees the compiled timeline's beat spans, never a voice take's raw
     alignment. Deliberately **not persisted** onto `release_anchors` (a cut-timeline-dependent
     fraction would go stale the moment the compiled timeline changes on a rerun) — only the
     word-level tier is persisted, via `apply_resolved_timing`.
   - Migration `0074`: `release_anchors.timing_exact` (nullable boolean — which tier last resolved
     this anchor). Applied and round-tripped live against Postgres; confirmed via
     `information_schema.columns`.

2. **Obstacle 5/7 enforcement in `app/documentary/detective/dispatch.py`** (the only place a
   detective-authored graphic is actually carved into the timeline, since Section D):
   - `_min_release_fraction(db, action)`: for an action's bound claims, the latest `char_end`
     fraction of any resolvable release anchor, via `anchor_fraction_within_span`.
   - `resolve_dispatchable_actions` now attaches `min_release_fraction` to each resolved record.
   - `place_detective_graphics` now starts the carved shot at
     `span["start"] + (span["end"] - span["start"]) * fraction` instead of always at
     `span["start"]` — a claim's supporting words must finish before its visual can appear, not
     merely share its beat. When the shift would leave under a second of room, the action is
     skipped as `insufficient_room_after_release_point` (reported, not silently dropped — same
     honesty discipline as every other `BUILDERS`/`skip` path in this module).
   - **Gating discipline preserved**: `fraction` defaults to `0.0` when no bound claim has a
     resolvable anchor (every claim made before this section, and any claim never given one) — the
     shot starts at exactly `span["start"]`, byte-for-byte the Section D behavior. Proven by
     `test_an_action_with_no_resolvable_anchor_keeps_the_old_beat_start_unchanged`.

3. **Audio-only audit (F3), narrow and honest**: Section B already enforces that a
   `DetectiveClaim`/`DetectiveQuestion`'s `text` is a real, non-empty, self-contained sentence —
   nothing in this feature ever shows a caption whose meaning exists *only* in a graphic label.
   Characterized by `test_claim_and_question_text_are_self_contained_without_the_visual`. This is a
   narrow, structural guarantee, not a full audio-only comprehension review — that is J's (Film
   Experience Reviewer) job.

## What was deliberately NOT built (named gaps, not hidden)

- **`text_revision_request` is still never persisted.** Section C's `persist_director_output`
  drops the Detective Director's `requests` field entirely (confirmed: grep for
  `text_revision_request` across `app/documentary/detective/` returns nothing). This repo has a
  real story-critique/rewrite workflow (`app/agents/critics.py`, `app/agents/story.py`) that this
  *should* eventually feed, per the doc's "propose a minimal correction through the existing
  text-revision workflow." Wiring that integration is a real, separate piece of work — persisting
  a request and routing it into an active critique/rewrite cycle touches a different subsystem's
  contract and deserves its own tested pass, not a bolt-on here. **F3 stays `IN_PROGRESS`** because
  of this gap specifically, not because the timing work above is incomplete.
- **Reading-time scheduling is not generalized beyond the detective board.** `reading_hold_seconds`/
  `appearances` give the board real, word-count-aware gating; no equivalent exists yet for an
  arbitrary dispatched tool (fact_card, floor_plan, and whatever D2 adds later) competing against
  dense unrelated narration elsewhere in the beat. Closing this fully needs a general
  speech-density signal this repo doesn't currently compute anywhere — out of scope for F, likely
  H's (production integration) to pick up once more tools are dispatchable.
- Full real-audio proof (an actual rendered take, real alignment, a real resolved anchor flowing
  through to a real render) was not attempted — this section's live evidence is fixture-based
  (synthetic alignment arrays, the same style this repo's own `tests/test_voice_render.py` already
  uses for `beat_times`/`words_from_alignment`). A true end-to-end real-audio proof belongs with
  Section K's real-film proof, once a case actually has both a detective plan and rendered audio.

## Checkpoint

- **F1** (anchor/word timing, release order including late qualifications) — `VERIFIED`. Both
  resolution tiers are real and tested; the carve-time enforcement is real, tested, and gated.
- **F2** (reading time / motion coordination) — `IN_PROGRESS`. The board's own mechanism is real
  and characterized; general cross-tool density-aware scheduling is not built.
- **F3** (audio-only intelligibility, text-revision routing) — `IN_PROGRESS`. The structural
  claim/question self-containment guarantee is real and tested; `text_revision_request` persistence
  and routing into the existing critique workflow is a named, deferred gap.
