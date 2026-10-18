# German film version checklist (docs/Story_Revol_de.txt, D1-D15)

Nothing was committed or pushed. **Owner instruction added in chat: written twice, inspected once, never more, hard-coded**
(exactly three model calls: rewrite, inspection, final; no second attempt, no inspector retry, no gate-repair call, no extra
call on resume). It is the same hard-coded single pass as for Persian (`SINGLE_CALL_AGENTS` in `app/language_pipeline/stages.py`,
now holding the three `.fa` and the three `.de` agents; keyed by agent name, not by language name).

Shared with the Persian work (reused, not copied): `stages.py` thin per-agent methods over shared request builders, `store.approve_automatically`,
`LanguageStageConfig` (`blocking_gates`, `automatic_approval`, and new here `report_names`), the one `fact_report.compare`.

| id | requirement | status | tests / evidence |
|----|-------------|--------|------------------|
| D1 | dependencies | DONE (code) / **BLOCKED_DEPENDENCY (live)** | Present: English master with automatic approval, agent map, shared `FilmInspection` model, stage config, non-blocking report, the fa agents. Missing for any live run: **an approved English master on case 8** (stopped, see `docs/english_pipeline_checklist.md`) |
| D2 | agents, roles, config | DONE | test_native_film_pipeline.py::test_the_three_agents_exist_exactly_as_specified[de], test_english_master.py::test_the_agent_map_selects_the_en_agents_for_en_and_the_generic_ones_for_the_rest; test_architecture.py passes. `used_by` names the real module `app/language_pipeline/stages.py` |
| D3 | prompts, contracts | DONE | Four files from the task file by script: **byte-identical** (sha256 guide `8a6b94c4...`, rewrite `9535ce6b...`, inspector `371ecc08...`, final `68540d8c...`). ::test_the_prompts_format_without_a_missing_or_leftover_placeholder[de], ::test_rewrite_gets_the_master_and_the_plan_and_no_evidence[de], ::test_inspect_gets_only_the_text_the_plan_and_the_counts[de], ::test_final_gets_the_master_as_a_reference_and_the_critique_of_the_inspector[de], ::test_an_invalid_inspection_fails_the_stage_without_a_second_call[de], ::test_a_cut_off_answer_fails_loudly_and_stores_no_text[de]. The task says an invalid inspector answer is "retried once"; the owner's chat instruction (never more) wins: it fails. **max_tokens:** shared 64,000 for the Sol writer roles; planning figure 1.47 tokens per German word, so a 70-minute text (about 9,800 words at 140 words per minute) is about 14k visible tokens. **Not measured on real German output (blocked)** |
| D4 | orchestration | DONE (tests) | ::test_a_native_language_is_written_twice_and_inspected_once_in_that_order_and_never_more[de], ::test_resume_after_a_failed_final_calls_only_the_final_and_bills_nothing_twice[de], ::test_the_final_is_stored_with_its_markers_as_a_new_version[de]. Not run live |
| D5 | gates and report | DONE (tests) | ::test_a_native_language_is_blocked_by_markers_and_length_only[de] (`language_stages.de.blocking_gates`, config). One report, extended with a per-language config `report_names` (en "added_and_missing", de "missing", fa "none") instead of a second report: ::test_the_german_report_finds_an_added_number_and_a_missing_name_and_reports_no_added_names (German "300.000" and "2,5" match "300,000" and "2.5"; the master's name "New Bedford" is reported missing; names added in German are never reported), ::test_the_report_is_stored_with_the_final_blocks_nothing_and_calls_no_model[de]. **Stated limits (also in the UI note):** numbers written out in words may be missed; names added in German are not reported (German capitalises all nouns). The model-backed gates and the cross-language check no longer block de; their code stays for ar |
| D6 | numbers, downstream | DONE (finding) | Spoken adaptation for de (`prompts/documentary/spoken/style_guides_de.md`): "Daten und Zahlen so, wie man sie spricht ("am 16. Juli 2007")", past tense, short sentences, no Nominalstil; the new film guide asks for "4. August 1892", period thousands and comma decimals, numbers to twelve as words: compatible. The TTS path has no digit, date, thousands-separator or abbreviation handling of its own (`voice_render.py`; the pronunciation editor handles words a voice may misread, not numerals). **Not verified: how ElevenLabs v3 reads German "300.000", "2,5" or abbreviations (no TTS run).** Proposed only if it misreads: a numerals pass in the spoken stage. Differences to know: the spoken guide wants "Konjunktiv I oder 'soll'" for allegations, while the film guide limits Konjunktiv I to where the source must stay visible, and the spoken guide addresses the listener with "wir"; both belong to the downstream stage and are not changed. Markers with umlauts and „“ ‚‘: ::test_act_markers_survive_german_text_with_umlauts_and_quotation_marks_exactly |
| D7 | prompt caching | DONE (provider) / not measured for de | Findings reused from the earlier work: Sol caches via `prompt_cache_key`, but unreliably for a large prefix (99.8 % in one probe, 5.7 % in the evidence-audit calls). A cache key is sent without a prefix (runner and provider changed earlier). de has its own system prompts, so it shares no cache with other languages; with exactly one call per stage there is no retry to reuse a prefix. No de call was made: no de cached-token figure exists. I did not run a fresh de probe |
| D8 | cost | DONE (estimate) | Estimate (`GET .../estimate?languages=de`): 3 calls, planning figures, not a price; see the final report. Actual: nothing spent for de |
| D9 | UI | DONE (mocked e2e) | The same language card as fa: three stages, critique, blocking gates, the report with its note (names missing from the German text), "approved automatically" with time, no approve button |
| D10 | tests | DONE | tests/test_native_film_pipeline.py is parametrized over fa and de (46 tests); detection tests for the agents, markers (`NATIVE RE-TELLING, NOT TRANSLATION`, `READ LIKE A LISTENER`, `FINAL LISTEN`, guide start `LANGUAGE GUIDE: GERMAN TRUE-CRIME STORYTELLING`) and placeholders pass. The older flow, checks, master and audit tests treated "de" as a generic language; they now put it back to generic in their fixture and the native tests restore the real config |
| D11 | no dead code | DONE | The old German guide was replaced (its content is gone; nothing else read it). The spoken-stage guide `style_guides_de.md` belongs to a later stage and was not touched (conflicts listed in D6). The generic prompts stay (ar uses them). The German stock-phrase list in `config/parameters/writer_metrics.json` was written by the assistant, not by a native editor. Nothing else was proven unused |
| D12 | quality assessment | **NOT DONE** | No German text exists (blocked) |
| D13 | completeness audit | DONE with the gaps listed | Gaps: D1/D14 live, D7 de figures, D12 |
| D14 | final live proof | **BLOCKED_DEPENDENCY** | Needs an approved English master. Once it exists: `POST /api/cases/8/language-pipeline/run {"languages":["de"],"requested_minutes":60}` |
| D15 | alignment patch | DONE | The paragraph was inserted before `{style_guide}` in en/rewrite_writer, en/final_writer, fa/rewrite_writer, fa/final_writer; the inspector paragraph at the end of "DO NOT MISDIAGNOSE" in fa/inspector (the en inspector already contained it, so it was left as it is, no duplicate). New sha256 (first 16): en/rewrite `bb66aebd293e12cb`, en/final `8190e5f6d5865aef`, en/inspector `12c95d7a6b85896c` (unchanged), fa/rewrite `146c261676e765e2`, fa/final `a84cae6cf607c0d4`, fa/inspector `addb4d0a15a58706`. No detection marker changed. ::test_the_alignment_patch_is_in_every_writer_and_inspector_prompt; the inspector-line parity test now compares the JSON, kinds and "Do not reward excessive caution" lines across en, fa and de |

## What stays unproven

Every live behaviour of the German stages: the quality of the German, real token use and the `max_tokens` fit, cached tokens, the report on real text, the automatic approval on a real final, and how TTS reads German numerals. They are covered by tests with a fake model only.

## Update after the Arabic task

The generic stage agents and the cross-language check were deleted (`docs/arabic_pipeline_checklist.md`, A11), so "de" no longer shares anything with a generic path; the older flow tests use en as their general-purpose language and widen en's `blocking_gates` where they exercise a model-backed gate.


## First live run on case 8 (2026-10-17): FAILED at the final, on the length gate

Source: the English draft row 41, approved automatically before the run. Rows: rewrite 48 (9,917 words, 70.8 min, EUR 0.185), inspection 49
(7 items, EUR 0.057), final 50 (8,101 words, 57.9 min, EUR 0.198, status needs_revision); 0 cached tokens on the German calls. The blocking gates:
markers passed, length failed (minimum 60 minutes at 140 words a minute, gate 8,148 words). No second call, no approval record. The German text was
not read closely; native quality is unjudged. How TTS reads German numerals is still unmeasured (no TTS run exists).

Since this run (fix task R1-R8): the inspector may use the kind "selection" and an invalid inspector answer is asked for once more
(a second failure fails the stage); the rewrite and final get `act_word_targets` and the final also `last_version_word_counts`;
the draft is shorter and less repetitive (draft prompt LENGTH section, a scene regenerated once); a final below the length minimum
gets at most one bounded length correction (`docs/english_pipeline_checklist.md`, R8). **This language has NOT been run again with
these changes**: the owner asked for English only until the English text has been read.
