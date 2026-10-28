# F2, F3 and C3 — reading time, the narration on its own, and a longer real passage

Closes (or narrows) three rows that Sections C and F left `IN_PROGRESS`:

| Row | Result | Why |
| --- | --- | --- |
| F2 reading time / speech do not compete | `VERIFIED` (fixture-based) | A dispatched card is held for the time its own text needs, never stretched into the next beat; a card that cannot be read in the room left, or that is read over dense unrelated narration, reports a scheduling request. |
| F3 narration understandable without the screen | `VERIFIED` (transcript-only) | The Director's text-revision requests are persisted against the exact passage; a deterministic audit flags claims only the screen says; the live Film Reviewer's narration findings reach the writing workflow's own item format. No audio was listened to. |
| C3 payoffs, connections, breathing room | `IN_PROGRESS` | On a real 8-beat, 84-sentence passage the payoffs and return reasons now hold; breathing room does not (nine text-dense actions in a row), because the Director is still handed an empty `asset_catalog`. |

## F2 - the reading-hold floor (`app/documentary/detective/reading.py`, `dispatch.place_detective_graphics`)

**The defect.** `place_detective_graphics` held every dispatched card for the per-kind constant
(`graphics.shots.hold_seconds`, 15 s for a fact card). The graphics layer already knows a text-heavy card needs
longer: its streaming schedule reveals words at the configured reading pace (`stream.words_per_second`) and
`planned_seconds` adds a margin. Measured with the real layout: a one-line fact card needs 15.0 s, a three-fact card
23.2 s of streaming (26.7 s planned) - carved for 15 s it fell below the minimum stream scale
(`min_speed_scale` 0.8, 15/23.2 = 0.65), so `finalize_graphics` dropped it later while the placement record said
`placed`.

**The fix.** `reading_need(kind, data, language)` returns the words the viewer is asked to read (the sum of the
real per-step word counts of the streamed layout, so it is language-aware), the planned seconds, and the fastest
length the card still streams in. Placement then:

* beat room >= planned: the card is held for its planned reading time;
* between the fastest pace and planned: placed in the room there is, with a `reading_time_compressed` request;
* below the fastest pace: not carved at all - `skip: insufficient_reading_time` with an `insufficient_reading_time`
  request (what `finalize` would have done silently);
* never past the beat's own end (the doc: "do not stretch a graphic into unrelated narration").

A dense card (>= `dense_card_words` visible words) whose window carries narration faster than
`dense_speech_words_per_second` that shares fewer than `related_overlap_min` of its content words gets a
`competing_dense_speech` request but is still placed. The narration over the window is each shot's `said`, in
proportion to the overlap (a shot carries its sentence, not per-word times - the same limit as Section F's
sentence-level fallback). The requests use the Visual Design Director's `timing_requests` shape and are returned in
`script["detective_graphics"]`. All numbers are in `config/parameters/detective_toolkit.json` (`reading`).

**Tests** (`tests/test_detective_reading_time.py`, 11): a dense card needs longer than a one-line card; it is held
for its reading time not the constant; a card that cannot be read is reported not stretched; a squeezed card
reports compression and stays in its beat; competing / related / slow / one-line / config-driven cases; a placed
dense card survives `finalize` where the old constant hold is dropped; no actions leaves the timeline untouched.
One Section F test fixture (a 20 s beat that left a late-released card under 10 s) was given a realistic 60 s beat:
its old "placed" result was a card `finalize` would have dropped.

**Limits.** Fixture-based - no real audio was rendered; the Director's real actions are `not_accepted_by_planner`
until needs.py's planner decision runs, so the placement was exercised through the real renderer, not on a live
plan.

## F3 - the narration on its own (`app/documentary/detective/text_revision.py`)

**Text-revision requests.** The Director's `requests` output was dropped everywhere. A `type: "text_revision"`
request now carries `unit_key`, the exact `passage`, the `problem` and a minimal `proposed_repair`;
`schema.validate_director_output` refuses one whose unit is unknown or whose passage is not the exact words of that
unit; `persist` stores it as a `DetectiveFinding` owned by `narration_workflow` (migration 0076's table - no new
table or migration), anchored to the exact words by a `ReleaseAnchor` (`anchor_unit`), idempotent on its id. The
Director is told the shape and rule through `schema_hints` (`prompts/documentary/detective/text_revision_rule.md`).
Other request types (asset, renderer capability, clarification) are still not persisted.

**The seam to the existing text workflow.** `revision_items` re-shapes the open requests into the critique item
`language_pipeline.review.critique_from` builds its revision round from (`severity`, `kind`, `act`, `quote`,
`problem`, `instruction`); a test runs the real `critique_from` on them. It is a conversion, not a wiring: the
language pipeline decides when a revision round runs, and no rewrite pipeline was built.

**The audio-only audit.** `audio_only_findings` checks every claim a scene's actions bind to (and the premises of a
bound question): its supporting narration spans must exist and contain at least `audio_only.min_supported_fraction`
(0.5) of the claim's content words. A claim with no supporting span, or whose words the narration does not say, is
something only the screen says. Each finding names the covered narration unit and a minimal correction;
`request_audio_only_revisions` persists them (idempotent). It feeds the Film Reviewer's `deterministic_validation`
together with the pacing findings.

**Tests**: `tests/test_detective_text_revision.py` (9).

**Limits.** The audit is lexical. On the real passage it flags abstractive claims the narration does state in other
words (e.g. "staged entry and an internal actor" against "the person already inside the home"); stemming did not
change that, so the threshold was not tuned to the data - the findings are advisory input to the reviewer, which
adjudicated them (it reported none of the five). What the audit cannot see, the live reviewer did: an unbound
pronoun ("He concluded..." with no one named) in the real narration. Transcript-only; no audio was listened to. The
live Director never emitted a `text_revision` request on this passage (once it asked for a `renderer_capability`,
caused by stale catalog text - fixed below), so that path is proven by test, not by a live request.

## C3 - a longer real sequence

**Source (read-only).** Case 15's narration is real and approved, and case 15 is over its EUR 300 cap
(EUR 323.0845101655945). Nothing was attributed to it: `docs/detective_toolkit_section_f2f3c3_evidence/live_c3.py`
refuses to run against it and compares its spend before and after. The passage is beats B13-B20 of blueprint 7
(story version 240, paragraphs 54-87): 84 sentences, one narration unit each, copied into a new scratch case. The
blueprint marks Q3 as opened at B13 and answered at B20, with the garage door, refrigerator, prints, casings and
dogs revisited between - an investigative thread that opens early and pays off late. The Director was given only
the text and a purpose per beat, not the blueprint's `opens`/`answers`.

| Run | Scratch case / graph | Calls | Cost | What happened |
| --- | --- | --- | --- | --- |
| 1 | 31 / 12 | map_film | EUR 0.0642 | failed: one claim listed a claim ID among its `entity_ids`; persistence refused it and the whole map was lost |
| 2 | 32 / 13 | map, direct, direct (repair 1), review | EUR 0.2240 | the repair loop (new) saved it; every question stayed `"opened"`; the reviewer's findings were stored with an empty problem text |
| 3 | 33 / 14 | map, map (repair 1), direct, direct (repair 1), review x3 | EUR 0.4317 | payoffs recorded after the hint; the reviewer's output failed validation (field names) until its hints were added |
| 4 | 34 / 15 | map, direct, review | EUR 0.1336 | no repair needed; no forgotten question, no return without a reason |

Spend over the four runs: EUR 0.8535. Case 15 after every run: EUR 323.0845101655945, unchanged.

## What the longer passage exposed (each fixed, each with a regression test)

1. **No repair when the model slipped.** `director._call` raised on the first violation. It now sends the output back
   in the Director's own `repair` mode with the exact issues, up to `detective_toolkit.max_repair_iterations`
   (1, the repo's per-domain convention), each attempt its own tracked run recording the issues it was sent; only
   the output that passes is persisted. Runs 2 and 3 needed it live. (`tests/test_detective_director_repair.py`, 5)
2. **Real bindings never resolved.** The Director binds an action to claims/questions as a flat list of ID strings,
   but every dispatch reader accepted only `{"claim_key": ...}` objects - so no real action resolved its claims, and
   Section F's release-fraction gating never fired on real output. One `binding_keys` reader now serves all of them
   and accepts both forms. (`tests/test_detective_bindings.py`, 4)
3. **A knowledge event's state arrived as a bare word** (`"opened"`, `"established"`), stored unreadable; Section E's
   `question_status_as_of` would crash on it. `persist` shapes it into `{"status": ...}`, memory refuses a question
   status outside `QUESTION_STATUSES` (the model wrote `"opened"`), and the Director is told the exact words and when
   to record a payoff. Run 2: all four questions stayed `"opened"`. Run 4: three `answered`, one
   `explicitly_unresolved`. (`tests/test_detective_question_state.py`, 8)
4. **Returns without a reason.** The doc requires `reuse_reason` for a meaningful return; the model supplied it in
   one run and not the next. The validator now refuses an action that re-shows a claim/question an earlier action
   showed without one, so an omission goes through the repair loop. Run 3 had four such returns; run 4 none.
5. **The reviewer's own field names drifted** (`finding_id`, `scene_ids`), and an earlier run's findings were
   persisted with an empty `observed_problem`. `review.build_input` now carries `schema_hints` (exact field names
   shared with the validator, vocabularies, the modes this review may claim) and a finding without its problem
   text is refused. (`tests/test_detective_reviewer_format.py`, 4)
6. **A reviewer finding could not be quoted.** It names the passage by unit (`B17-4`); for a narration-workflow
   finding that now becomes an exact anchor, so `revision_items` yields the words.
7. **Stale catalog text** told the Director `floor_plan` was dead and that Section D "must" do things D has done; it
   asked for a renderer capability because of it. The availability text now states what each tool needs.
8. A bug in the new checker itself (flat bindings matched both lookups, so a return listed each ID twice).

## C3 - the result on the real passage (run 4, graph 15)

The deterministic checker (`app/documentary/detective/pacing.py`, `tests/test_detective_pacing.py`, 9) reads the
persisted plan; the live Film Reviewer (`plan_audit`, inspection modes `["plan", "transcript_only"]`) judged the
same sequence and returned `revise`. Both are also fed to the reviewer as `deterministic_validation`.

| C3 point | Result | Evidence |
| --- | --- | --- |
| Question payoffs - answered, qualified or honestly left open, none forgotten | holds | 4 questions: 3 `answered`, 1 `left_open_by_text` ("Where is the 9mm handgun...", `explicit_unresolved`, matching a narration that never recovers it); 0 forgotten. Run 2, before the fixes: all four stuck at `"opened"`. |
| A return says what it adds | holds | 0 returns without a `reuse_reason`; the actions that revisit the missing-handgun and flight-trail questions say what they add (an update, a resolution). |
| Meaningful connections | holds, with a caveat | 5 relations; the reviewer's summary names the evidence chain from lock mechanics to digital records. The reviewer judged the logic "exceptionally clear". |
| Breathing room - a picture, a place or a hold between text-dense cards | **does not hold** | 9 consecutive text-dense actions (unknowns, four evidence checks, two forensics, conflict, recap) with no picture, place or hold. Flagged by the checker and, independently, by the reviewer (`visual_design_director`, minor). |
| Human interest | not shown | no photo was offered, so none was chosen. |

**Why breathing room fails, and what closes it.** `director._build_input` still passes `"asset_catalog": []`, and the
scratch case has no approved pictures, so the Director could choose a text tool and nothing else; the catalog entry
for `photo` now says it only exists for pictures listed in the asset catalog. Closing it means populating
`asset_catalog` from the case's approved `VisualAsset` library (a Section D/H integration) and re-running on a case
that has pictures. Case 15 has them but is over its cap, so it cannot be re-run here. That is the one reason C3
stays `IN_PROGRESS`.

**What the reviewer found that no deterministic check can**, in the real narration of case 15: an unanchored
pronoun (`B17-4`: "He concluded that both casings had been fired from the same firearm" with no examiner named) and
near-duplicate consecutive sentences (`B15-3`/`B15-4`). Both were routed to `narration_workflow`.

## Honest limits

* F2 is fixture-based: the placement is tested through the real renderer, but no audio was rendered and the real
  actions were not yet accepted by the planner (`not_accepted_by_planner`).
* F3 is transcript-only and the audit is lexical (see above); the Director's text-revision request was proven by test,
  not produced live; the language pipeline is not wired to consume the items.
* C3: one run per configuration - the model's output varies run to run (run 2 supplied reasons and run 3 did not),
  so the enforcement is in the validator, not in the hint alone. Four runs of one passage, not a corpus.
* Each repair attempt is a paid call (run 3's `direct_scenes (repair 1)` cost EUR 0.20); `max_repair_iterations`
  bounds it, and the hints exist to make most first attempts pass.

## Reproduce

`PYTHONPATH=. python3 docs/detective_toolkit_section_f2f3c3_evidence/live_c3.py` (paid; new scratch case; refuses
case 15) and `PYTHONPATH=. python3 docs/detective_toolkit_section_f2f3c3_evidence/c3_check.py GRAPH_ID` (read-only).
Set `DETECTIVE_DIRECTOR_RAW_DUMP=<file>` to keep the Director's last raw output.
