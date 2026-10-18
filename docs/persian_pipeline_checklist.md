# Persian film version checklist (docs/Story_Revol_fa.txt, P1-P14)

Source: `docs/Story_Revol_fa.txt`. Nothing was committed or pushed. No Persian task existed before, so there are no old rows to map.

**Owner instruction added in chat: Persian is written twice and inspected once, never more, hard-coded.** Implemented as exactly three
model calls for fa: rewrite (writing 1), inspection, final (writing 2). No second attempt at a stage, no retry of an invalid
inspector answer, no gate-repair call, no automatic extra call on resume (`SINGLE_CALL_AGENTS` / `is_single_pass` in
`app/language_pipeline/stages.py`, keyed by the three agent names, not by language name; the number is in code on purpose, as asked).
A stage that fails is not repeated; a resume only runs the stages that have no row yet. The owner's manual "improve" action
(stage 4 again on request) is the one thing left that can add a call, and only when the owner asks.

## Status

| id | requirement | status | tests / evidence |
|----|-------------|--------|------------------|
| P1 | dependencies | DONE (code) / **BLOCKED_DEPENDENCY (live)** | Present: the English master with automatic approval (`master.py`, `record_approval`), `language_agents` map, `language_stages`, the shared `FilmInspection` Pydantic model, `post_master_fact_report`. **Missing for the live run: an approved English master on case 8.** The loop stopped (7 open high fact items) and a stopped master blocks every language by design. Proof: job 27 (`languages ["fa"]`) failed with `MasterStopped`, no paid call. I did not bypass the stop rule |
| P2 | agents, roles, config | DONE | test_persian_pipeline.py::test_the_three_persian_agents_exist_exactly_as_specified, ::test_the_other_languages_do_not_use_the_persian_agents; test_architecture.py passes. `language_agents.fa`, `language_stages.fa` in config. `used_by` names the real module `app/language_pipeline/stages.py` (not the non-existent `app/agents/language_pipeline.py`). Roles/models unchanged (Sol, high); same-model exception not widened |
| P3 | prompts and contracts | DONE | The four files written by script from the task file: **byte-identical** (sha256 guide `33f6a941...`, rewrite `80740200...`, inspector `d7f4f2fb...`, final `6877fb55...`). ::test_the_persian_prompts_format_without_a_missing_or_leftover_placeholder, ::test_rewrite_fa_gets_the_master_and_the_plan_and_no_evidence, ::test_inspect_fa_gets_only_the_persian_text_the_plan_and_the_counts, ::test_final_fa_gets_the_master_as_a_reference_and_the_critique_of_the_inspector, ::test_an_invalid_persian_inspection_fails_the_stage_without_a_second_call, ::test_a_cut_off_persian_answer_fails_loudly_and_stores_no_text. Request keys are ordered stable first (language, plan, ranges, english_master, then last_version, critique). **max_tokens:** the existing 64,000 for the Sol writer roles; the planning figure is 1.71 tokens per Persian word, so a 70-minute text (about 9,450 words) is about 16k visible tokens. **Not measured on real Persian output (blocked)** |
| P4 | orchestration | DONE (tests) | ::test_persian_is_written_twice_and_inspected_once_in_that_order_and_never_more, ::test_resume_after_a_failed_final_calls_only_the_final_and_bills_nothing_twice, ::test_the_persian_final_is_stored_with_its_markers_and_a_new_version_beside_the_old_ones. The master is never rewritten for a Persian failure (separate rows). Not run live |
| P5 | gates and report | DONE (tests) | ::test_persian_is_blocked_by_markers_and_length_only, ::test_the_numbers_report_normalises_digits_and_finds_an_added_and_a_missing_number, ::test_the_numbers_report_is_stored_with_the_final_and_blocks_and_calls_nothing. Config `language_stages.fa.blocking_gates` = ["markers","length"]. The other gates' code stays (de, ar). The cross-language check leaves fa out. **Names are not compared** (transliteration between scripts cannot be matched reliably); the report and the UI say so, and that a number written out in Persian words may show as missing |
| P6 | numbers, digits, downstream | DONE (finding recorded) | Finding: the downstream spoken adaptation for fa (`prompts/documentary/spoken/style_guides_fa.md`) writes colloquial Tehrani Persian and says "write dates and numbers the way they are said", so it re-tells numbers itself; the TTS path (`voice_render.py`) has no digit handling of its own; `production/script.py::_spoken_at` normalises Persian digits to Western when matching years, so years in Persian digits are handled. **No change made.** Open point for the owner: that spoken guide asks for a colloquial register while the new film guide asks for standard Persian ("no routine slang"); they belong to different stages, but the second re-telling will undo the first guide's register. Not verified: how ElevenLabs v3 reads Persian digits (no TTS run). Marker syntax with RTL text: ::test_act_markers_survive_right_to_left_text_exactly |
| P7 | prompt caching | DONE (provider), not measured for fa | Same provider path as the English work (live probes earlier: Sol caches, but erratically for large prefixes: 99.8 % in one probe, 5.7 % in the audit calls). A `cache_key` is now sent even without a prefix (runner and provider changed, test `test_a_cache_key_without_a_prefix_is_sent_to_the_provider`). fa shares no cache with the other languages (its own system prompts); with exactly one call per stage there is no retry to reuse a prefix, so the expected saving within fa is small. No fa call was made, so no fa cached-token figure exists |
| P8 | cost | DONE (estimate) | Estimate printed before the run (`GET .../estimate?languages=fa`): 3 calls (1 per stage), about EUR 0.46 (rewrite 0.17, inspect 0.09, final 0.20); planning figures, not a price. Actual ledger: nothing spent for fa |
| P9 | UI | DONE (mocked e2e) | Language card for fa: stage table, critique, blocking gates ("Blocking gates: markers, length"), the numbers report with the note that names are not compared, "approved automatically" with time, no approve button (`automatic_approval`). e2e app.spec.ts "Language pipeline". Not shown live (no fa data) |
| P10 | tests | DONE | tests/test_persian_pipeline.py (28), plus the adapted flow, checks, config and master tests. Automatic approval and "nothing beyond the final text starts": ::test_the_final_gets_an_automatic_approval_record_and_nothing_beyond_it_starts, ::test_a_final_whose_gates_failed_is_not_approved, ::test_the_english_and_other_finals_are_not_approved_automatically. Detection tests for the agents, markers and placeholders pass |
| P11 | no dead code | DONE | The old Persian guide was replaced by the new one (its content is gone; nothing else read it). Searched for other users of the old rules: none. The generic rewrite/inspector/final prompts stay (de, ar). The colloquial `style_guides_fa.md` belongs to the spoken stage and is not touched. No removal beyond the guide replacement was proven safe |
| P12 | quality assessment | **NOT DONE** | No Persian text exists, so there is nothing to read or measure. The Persian stock-phrase list in `config/parameters/writer_metrics.json` was written by the assistant, not by a native speaker |
| P13 | completeness audit | DONE with the gaps listed | Gaps: P1/P14 live, P7 fa cache figures, P12 |
| P14 | final live proof | **BLOCKED_DEPENDENCY** | Needs an approved English master. Everything else is in place; once it exists, `POST /api/cases/8/language-pipeline/run {"languages":["fa"],"requested_minutes":60}` runs the three Persian calls and the automatic approval |

## Findings for the owner

1. **The fa inspector prompt lacks the two "do not reward excessive caution" sentences** that the English inspector prompt has; the JSON line and the allowed-kinds line are identical to the English one (test compares them). Whether the sentences should be added to fa is your call; I did not edit your files.
2. The generic stage-2 reading changed for fa as for en: the film stages get the master and the plan only; fa no longer takes part in the cross-language facts check (it has a stage config entry).
3. Spoken-adaptation register conflict (P6).

## What stays unproven

Every live behaviour of the Persian stages: the quality of the Persian, real token use and the max_tokens fit, cached tokens, the numbers report on real text, the automatic approval record on a real final. They are covered by tests with a fake model only.

## Update after the Arabic task

The generic stage agents, `generic_agents`, the cross-language check and the automatic glossary proposal were deleted (see `docs/arabic_pipeline_checklist.md`, A11). The `compare_numbers` function named above is now the single `fact_report.compare(..., names="none")`. The fa inspector now has the two "do not reward excessive caution" sentences (German task, D15), so finding 1 above is resolved.


## First live run on case 8 (2026-10-17): FAILED at the final, on the length gate

Source: the English draft row 41 (15,426 words), approved automatically before the run. Rows: rewrite 45 (12,441 words, 92.2 min, EUR 0.207,
28,800 of 28,920 input tokens cached), inspection 46 (6 items, EUR 0.072, 0 cached), final 47 (7,008 words, 51.9 min, EUR 0.214, 0 cached,
status needs_revision). The blocking gates: markers passed, length failed (the minimum is 60 minutes at 135 words a minute). No second call
was made and no approval record was written. The final starts "Thursday morning, 4 August 1892, in Fall River..." (translated); it was not read
closely by the assistant, so native quality is unjudged. Sol cut a 92-minute rewrite to 52 minutes in the final.

Since this run (fix task R1-R8): the inspector may use the kind "selection" and an invalid inspector answer is asked for once more
(a second failure fails the stage); the rewrite and final get `act_word_targets` and the final also `last_version_word_counts`;
the draft is shorter and less repetitive (draft prompt LENGTH section, a scene regenerated once); a final below the length minimum
gets at most one bounded length correction (`docs/english_pipeline_checklist.md`, R8). **This language has NOT been run again with
these changes**: the owner asked for English only until the English text has been read.
