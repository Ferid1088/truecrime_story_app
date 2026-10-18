# English pipeline checklist (docs/Story_Revol_en.txt, simplified: E0-E10)

Decision change (one sentence): the fact-check loop (inspect, revise, repeat) that produced an "English master" is retired; after the
draft, no model checks facts in English except the one-pass report-only evidence audit, so the guards are the evidence-bound drafting,
the deterministic gates, the audit and the non-blocking numbers-and-names report.

Nothing was committed or pushed. Live = the real stack (API 8001, UI 3001, PostgreSQL, real providers) on case 8 ("Lizzie Borden axe murders").

## The method now

1. **Draft** (`language.draft`, Sonnet 5.5, prompt `prompts/language_pipeline/draft_writer.md`): scene by scene, one call per key beat, no retry,
   cached evidence prefix, a `told_so_far` ledger. A draft is the source only if its prompt sha256 matches the current prompt.
2. **Evidence audit** (`english.evidence_audit`, Sol): chunks of two acts, report only, one retry on invalid JSON, failures recorded and never blocking.
3. **Automatic draft approval** (`StageStore.approve_draft_automatically`): `approved_by "automatic"`, time, draft id, cost, audit status in the notes.
   The audit does not gate it. No language starts before it (`_stages_2_to_4` raises `StageFailed(DRAFT)` without an approval record).
4. **Per language (en, fa, de, ar) exactly three Sol calls**: `language.rewrite.<l>`, `language.inspect.<l>`, `language.final.<l>`; no retry, no revision round.
   Blocking gates: markers and length. Non-blocking: the numbers-and-names report. Automatic approval of a passing final. Nothing starts beyond the final.
5. **Retired** (status `retired`, UI label "retired method"): rows of kinds `master_inspect`, `master_revise`, `english_master` and the aborted draft 40.

## Rows

| id | requirement | status | test / evidence |
|----|-------------|--------|-----------------|
| E0 | backup before deletion | DONE | `~/truecrime_backup_2026-10-17/deleted_backup` (deleted files) and `~/truecrime_backup_2026-10-17/before_E1` (pre-change copies), outside the repository |
| E1 | no fact loop in English | DONE | tests/test_english_stages.py; code and prompts of the loop deleted (`english/fact_inspector.md`, `reviser.md`, `ensure_master`) |
| E2 | no model validators in the English path | DONE | tests/test_language_pipeline_config.py (en `blocking_gates` = markers, length) |
| E3 | en through the same mechanism | DONE (code, live) | tests/test_native_film_pipeline.py[en]; live: rows 42-44 on case 8 (three calls, gates ran, no call beyond the final) |
| E4 | prompt wording | DONE | tests/test_language_pipeline_prompts.py |
| E5 | retire old rows by code path | DONE | `StageStore.retire`, `retire_loop_rows`; tests/test_english_stages.py |
| E6 | tests | DONE | full suite, see the final report |
| E7 | docs | DONE | this file, ROADMAP.md |
| E8 | live run | **RAN, English FAILED the length gate** | below |
| E9 | read the text, before/after | DONE (below) | rows 41 and 44, `app/services/writer_metrics.py` |
| E10 | audit sentence by sentence, self-review | DONE | this table and the diff review in the final report |

## Live evidence (case 8, 2026-10-17)

| stage | row | words | minutes | cost EUR | cached tokens | note |
|-------|-----|-------|---------|----------|---------------|------|
| draft | 41 (Sonnet) | 15,426 | 102.8 | 8.262 | not recorded here | one call per scene; draft 11 stays history; row 40 (aborted, EUR 0.38) retired |
| evidence audit | in row 41 notes | n/a | n/a | 0.729 | n/a | status ok, 27 items, report only |
| draft approval | row 41 | n/a | n/a | n/a | n/a | `approved_by automatic`, event `draft_approved` came before the first `rewrite` event |
| en rewrite | 42 | 6,793 | 45.3 | 0.128 | 0 | |
| en inspection | 43 | 41 (JSON) | n/a | 0.039 | 0 | 4 items, valid |
| en final | 44 | 7,525 | 50.2 | 0.151 | 0 | markers passed, **length failed** (floor 60 min); status needs_revision; no approval; stopped |

English is therefore FAILED, as the rules require: no second attempt was made.

## E9: before and after (draft 41 vs final 44)

| metric | draft | final |
|--------|-------|-------|
| words | 15,437 | 7,540 |
| mean sentence length / variance | 14.52 / 53.84 | 12.98 / 42.25 |
| one-line paragraph share | 0.117 | 0.029 |
| repeated trigram rate | 0.367 | 0.147 |

The draft repeats itself heavily (trigram rate 0.367) and is 102.8 minutes against a 60-minute target; some scenes overran their slice
(act 3 scene 1 returned 1,341 words against 357). The final removes the repetition and the one-line paragraphs but cuts the text to
about 50 minutes, under the length floor. Ending: the draft ends on "Someone went up to that room after her. No account, no trial and no century of argument has shown who it was."; the final ends on "No account, trial, verdict, or century of argument has shown who climbed those stairs."

**Could not be judged:** whether the final is faithful beyond the 27 audit items (the audit is not exhaustive and its items were not hand-checked),
how the text sounds read aloud, and whether the cuts lost anything an editor would keep.


## Fix task R1-R8 and the second live run (2026-10-17)

The first live run failed the length gate in every language (final 50-58 minutes against a 60-minute floor) and the Arabic
inspector returned the kind "selection". The fixes, each with its test and live evidence:

| id | requirement | status | test / evidence |
|----|-------------|--------|-----------------|
| R1 | inspector kind "selection"; an invalid inspector answer is asked for once more | DONE | tests/test_act_lengths.py::test_the_two_inspector_lines_are_identical_in_all_four_files; tests/test_native_film_pipeline.py::test_the_selection_kind_is_allowed_and_an_unknown_kind_is_not, ::test_a_first_invalid_inspection_is_repeated_once_and_the_second_answer_is_used, ::test_an_invalid_inspection_is_asked_for_once_more_and_a_second_failure_fails_the_stage. Live: the English inspection of the second run was valid at the first attempt (1 call), so the repeat was not exercised live |
| R2 | draft length discipline: new LENGTH section, delivery check, one regeneration of a scene over 1.3 times its budget or retelling (trigram overlap above 0.3) | DONE | tests/test_act_lengths.py (the prompt text, the regeneration with the reason, never a third call, the overlap metric). Live: 2 of 36 scenes were regenerated (act3 scene 2: 1,097 words against 357, then 143; act3 scene 3: 590 then 133), the draft is 12,802 words (85.3 min) against 15,426 before, repeated-trigram rate 0.220 against 0.367 |
| R3 | act word targets for rewrite and final in all four languages, last_version_word_counts, non-blocking per-act report | DONE | tests/test_act_lengths.py (targets add up to the middle of the range, cap, report, the eight prompt files identical, key order). Live: see the table below. In the English rewrite prompt there is no LENGTH heading (the rule sits inside HARD LIMITS), so the ACT LENGTHS section stands directly before HARD LIMITS; in fa/de/ar it stands after the LENGTH section |
| R4 | measured speaking rate | NOT MEASURABLE | no ElevenLabs call in provider_calls, no host_scenes or production_scripts row, no audio file under data/cases or data/benchmarks. Configured rates stay: en 150, de 140, fa 135, ar 130 (language_pipeline.words_per_minute); pricing.json assumes 150. Every "minutes" figure in this project is words divided by these rates, not a measured duration |
| R5 | English-only re-run | DONE (table below) | job 33 |
| R6 | audit triage and audit of the final | DONE (below) | job 34 |
| R7 | housekeeping | DONE | the fa, de and ar checklists carry the first run; frontend type check and mocked Playwright specs pass |
| R8 | bounded length correction | DONE (code, tests); NOT RUN LIVE | tests/test_length_correction.py (23 tests). The English final of the second run was not below the floor, so by rule it was not run |

### R5: English only, case 8

| stage | row | words | minutes | EUR | cached tokens |
|-------|-----|-------|---------|-----|---------------|
| draft (Sonnet) | 52 | 12,802 | 85.3 | 7.147 | 1,938,424 of 5,456,131 (38 calls: 36 scenes and 2 regenerations) |
| evidence audit | in 52 | n/a | n/a | 0.722 | 10,240 of the audit input; 34 items |
| draft approval | 52 | n/a | n/a | n/a | automatic, 2026-10-17 18:55:08, before the first English call |
| rewrite | 53 | 8,324 | 55.5 | 0.137 | 0 of 25,166 |
| inspection | 54 | 10 items | n/a | 0.060 | 0 of 17,138 |
| final | 55 | 9,072 | 60.5 | 0.182 | 0 of 37,293; gates markers and length passed; automatic approval 19:01:12 |

Per act (draft words, target, final words, share): act1 1,024 / 932 / 956 / 1.03; act2 1,721 / 1,362 / 1,300 / 0.95; act3 946 / 1,291 / 1,240 / 0.96;
act4 1,761 / 1,433 / 1,307 / 0.91; act5 1,696 / 1,147 / 1,064 / 0.93; act6 3,144 / 1,936 / 1,687 / 0.87; act7 2,510 / 1,649 / 1,518 / 0.92. All within 15 percent.
Repeated-trigram rate draft 0.2204, final 0.1555; one-line paragraph share draft 0.141, final 0.053; sentence length mean 13.46 / 13.17, variance 42.25 / 38.66.
The complete final script is written to `/Users/feri/truecrime_case8_english_final_v55.txt` (outside the repository).
Case 8 spend after the run: EUR 85.10 of 300.

### R6: audit triage (corrected on 2026-10-18)

**Correction.** The first version of this section called the ash-pile, the officer-contradicted-the-handle, the stairs and the Churchill findings "true findings".
That was wrong: I had checked them against the fact claims only. The writer also receives the contradictions list (C008 "the defense elicited testimony that the basement contained a
massive pile of ash that coated everything equally", C012 "Another officer contradicted the testimony about the hatchet handle", C013 first said she was on the stairs, C014 she told
Adelaide Churchill and the maid that Abby was upstairs), the timeline and the approved context, and the audit did not receive them (`SharedMaterial.audit_evidence()` held facts and
sources only). So the auditor reported every claim that came from those lists as unsupported. Fixed: the audit now receives the same timeline, contradictions and approved context
(tests/test_evidence_audit.py::test_the_auditor_reads_the_contradictions_the_writer_read_so_a_claim_from_them_is_supported). The audits reported below ran before the fix.

First audit (draft 41, 27 items: 8 high, 18 medium, 1 low), the 8 high and the first 5 medium items re-checked against the complete evidence block the writer received:
- false alarms (the claim is in the supplied contradictions): high 1 (ash pile), 3 (officer), 4 (first account on the stairs), 8 (Churchill). 4 of 13.
- arguable or partly true: high 2 ("recorded denial": she denied laughing upstairs, but the jury did not hear her inquest words), 5 (the source calls the statements contradictory; "cannot both describe the same morning" is stronger),
  7 (the facts give the inquest as August 8 and as August 9-11), medium 12 (the Rebello half is supported, the negative claim is not stated), 13 (a fact says she was not allowed to bring her attorney to the preliminary hearing; the script said the inquest).
- true findings: high 6 (nothing says Russell stayed in the house), medium 9 (Abby "above her the whole time" is an inference), 10 ("no source offers evidence" against "no conclusive evidence"), 11 ("first name on most lists" ranks without a source).
- false-alarm rate: 4 of 13 clearly, 5 of 13 with the arguable high 5; 9 of 13 are not simply true findings. The auditor's evidence gap, not its judgment, caused the four clear ones.

Audit of the second final (row 55, same prompt, report only, job 34): status ok, 23 items (1 high, 17 medium, 5 low), EUR 0.668. The items about the ash pile and the officer (3, 6, 18) are the same false alarms.
Claims the final added or sharpened that draft 52 did not contain (quoted from the final; not checked against the contradictions list, so some may be supported):
- "She was Andrew's wife, the woman who ran the household, and a stepmother to two daughters who were now adults." (draft: "the woman of that household")
- "Emma has also been proposed, often in theories involving Morse, although she was in Fairhaven, roughly 15 miles away." (no such sentence in the draft)
- "It rests on no document in this record and does not explain Abby's death." (draft: "No document is cited for any of them")
- "She chose not to give trial testimony." (the facts say she did not testify, not why)
- "Its purpose was to gather sworn testimony about how Andrew and Abby had died." (no such sentence in the draft)
- "Her testimony placed Lizzie upstairs at a moment when Lizzie maintained she had been downstairs." (the draft says only that Bridget heard laughter)
What the audit cannot find: anything outside the supplied material, claims it chose not to flag (recall unmeasured; it reports at most 25 items per two acts), omissions that are not key facts. The false-alarm rate above is one reader's check of 13 items.
The deterministic numbers-and-names report of the final lists the figure 4999 (in "$4,999 ahead") as added, and its name lists mirror each other on curly against straight apostrophes; that is a weakness of the name comparison, not a finding about the text.

## Editorial revision of the English prompts (2026-10-18)

Replaced the editorial instructions of `draft_writer.md`, `english/evidence_audit.md`, `en/rewrite_writer.md`, `en/inspector.md`, `en/final_writer.md` and `guides/en.md` with the owner's text, merged with the technical contracts;
reconciled `prompts/agents/story/design.md` (the plan). Changes beyond the owner's text, with the reason:
- draft: the call writes ONE scene, so `{target_words}` (the whole 90 minutes) is given as context and the budget is `unit_target_words` in the input (the owner's text assumed one whole-script call); `correction` is named in INPUT; the strict evidence rules (no outside memory, exact names and numbers, no implied guilt) stay inside FACTUAL DISCIPLINE; a rule against narrating the film's own structure ("this beat", "the film will show") was added after the live evaluation found it.
- audit: the JSON contract and the kinds are kept; the owner's categories map onto them (legal status and chronology to `wrong_detail`, an unsupported motive to `unsupported`, a contradiction resolved without support to `contradiction`); a claim that cannot be verified is `unsupported`, one the material shows to be wrong is `wrong_detail` or `contradiction`. Its input now includes the timeline, the contradictions and the approved context (see R6).
- rewrite and final: the ACT LENGTHS sections (identical in all four languages, tested) stay; a short HARD LIMITS block (truth, contradictions, order, markers) stays because no later stage checks facts; restored material must be substantive and come from the master.
- inspector: the owner asked to use no kind "selection" unless the schema supports it. It does (R1), so selection is used. The inspector has no evidence and no factual kind, so an inconsistency inside the script is reported as `spoken`. It sees no per-act targets and is told not to invent them. The two lines after the JSON shape stay identical in all four languages.
- guide: the sentence "later versions will adapt it" was dropped (the film text has no later versions); the concrete presentation conventions (dates, numbers, names) were kept as PRESENTATION; the language examples were dropped.
- plan: "An act ends on a fact or a question, never on a summary" became "...a development, a consequence or an open question, as this act requires. Not every act needs a question, and none ends on a recap"; the arc's last act no longer ends "on a strong factual image or question"; assigning evidence is "choosing, not cataloguing" (never what user_instruction requires, a contradiction that matters or a protected reveal); acts are not equal; a returning event is assigned only for what is new.
Tests: tests/test_prompt_contracts.py (labels the prompts name equal the schemas' labels, placeholders, inputs, plan prompt), and the existing pinned wording updated.

Live evaluation on case 8 (job 35, English only): the new draft is 10,374 words (69.2 minutes by the configured rate), EUR 9.35, row 56, status `needs_revision`: the application requires a draft at least as long as the longest final it feeds (10,500 words), so the run STOPPED at the draft check; no audit, no approval, no film version. Draft 56 against draft 52: repeated-trigram rate 0.172 against 0.220, one-line paragraph share 0.070 against 0.141, sentence-length variance 59.9 against 42.3, no scene retold more than 23 percent of earlier wording (draft 41: up to 76 percent), 2 of 36 scenes regenerated. Read in full by the assistant: the story is told in clear connected paragraphs, but the burned dress is told three times, the verdict section recaps the hatchet, the blood and the dress once more, and the scene-by-scene calls leak structure ("this part", "what remains for this beat", "the film will show"). The last two are what the new rule in the draft prompt addresses; that revised prompt has NOT been run. The complete draft is at `/Users/feri/truecrime_case8_english_draft_v56.txt` (outside the repository).

## Third live run on case 8: the owner's prompts (2026-10-18, jobs 36 and 37)

Prompt files were the owner's versions (nothing overwritten); their sha256 are recorded in `~/truecrime_backup_2026-10-17/owner_prompts_2026-10-18/`. Three tests that pinned my earlier wording
(tests/test_act_lengths.py, tests/test_prompt_contracts.py: the draft LENGTH sentences, the verbatim ACT LENGTHS paragraphs, the "never refer to this part" rule) were updated to the owner's wording; the parity checks across the four languages were kept.
Draft 57 (Sonnet, 11,330 words, 75.5 min, EUR 9.355, 37 calls, 2,673,620 of 7,661,533 input tokens cached), audit 35 items (12 high, 23 medium, EUR 0.876), automatic approval 23:45:47,
rewrite 58 (8,986 words, EUR 0.151), inspection 59 (11 items, EUR 0.067), final 60 (8,693 words, 58.0 min: length gate failed, floor 8,730),
bounded length correction (6 acts sent, EUR 0.149) -> final 61 (9,297 words, 62.0 min, gates passed, automatic approval 23:55:39). Audit of final 61: 16 items (3 high, 12 medium, 1 low), EUR 0.793.
Repeated-trigram rate: draft 0.1856, final 60 0.0984, final 61 0.1102; one-line paragraph share 0.077 / 0.042 / 0.046. Files: `~/truecrime_case8_english_draft_v57.txt`, `~/truecrime_case8_english_final_v61.txt`.
The corrected acts act4 and act7 carry the "likely padded" flag (trigram rate 0.029 to 0.037 and 0.008 to 0.012); act4, act7 and four others were not inspected after the correction.
