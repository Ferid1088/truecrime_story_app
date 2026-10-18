# Language pipeline checklist (docs/story_change_1.txt, T1-T17)

Reading recorded: the owner's "fr" means **fa** (Persian). The languages are the existing catalog's
`multilingual.production_languages` = fa, en, de, ar. French is not added.
The "no commit" rule of this task is followed; commit `b742051` (the eight prompt files of
docs/story_change.txt) was made before this task text existed. Nothing was pushed.

Live = on the real stack (UI 3001, API 8001, worker, PostgreSQL, real providers), case 8
("Lizzie Borden axe murders"). Status: DONE = test and live evidence; PARTIAL = see "not proven".

## Live run (job 17 + job 18, case 8)

* shared draft v11: claude-sonnet-5-5, 14,357 words / 95.7 min, 46 calls (one per key beat, 91 % of input tokens cached), EUR 2.56
* per language (fa / en / de / ar), all gpt-6.1-sol, reasoning_effort high:
  rewrite 7,990 / 8,888 / 8,276 / 7,599 words; final 8,793 (65.1 min) / 9,514 (63.4) / 8,570 (61.2) / 7,793 (59.9 min);
  inspector items 34 / 33 / 38 / 38; final version ids 21 / 22 / 24 / 23; all gates passed
  (en, de, ar after one stage-4 gate repair; ar once for `grounding`; de once for the cross-language check)
* cross-language facts check round 0 failed (de implicated), stage 4 repaired de, round 1 passed; 0 failing, 14 info items
* originality report per final: 78 transcript wordings compared; longest shared run 0 / 11 / 6 / 6 words; 0 near-verbatim sentences
* owner gate: the fa final was approved in the UI (screenshot `docs/evidence/language-pipeline-bottom.png` after `APPROVE=1`); en/de/ar await approval
* ledger (all attempts, incl. the failed first runs): case 8 spent EUR 44.00 of the EUR 300 cap; 109 large calls (Sonnet + Sol) over all runs
* clean-run price after this session's fixes is shown by the estimate endpoint; the estimate printed BEFORE the first live run was 13 large calls, EUR 4.21 (planning figures). It undercounted: scene-by-scene draft (36 calls) and the gate validators (the cheap grounding validator read a 106k-token pack per batch: EUR 2.2-2.9 per language). Fixed: validators now read a 19k-token evidence list; the estimator counts scenes and gates. Never an actual price.
* screenshots: `docs/evidence/language-pipeline-top.png`, `-draft.png`, `-bottom.png`, `videos-use-and-rights.png`

## Rows

| id | requirement | status | tests | live evidence |
|----|-------------|--------|-------|---------------|
| T1 | sol alias, 4 roles/agents/prompts/routing, effort real, pricing | DONE | test_language_pipeline_config.py, test_language_pipeline_provider.py::test_reasoning_effort_is_sent_and_proven_by_the_response | GET /v1/models lists `gpt-6.1-sol`; `reasoning_effort` accepted (bogus -> HTTP 400 listing none/minimal/low/medium/high/xhigh/max), `completion_tokens_details.reasoning_tokens` returned; ledger rows carry reasoning tokens and effort; price from https://apimaster.ai/api/pricing (sol 2/10 USD per 1M, cache read 0.1): the gateway reports no cost for Sol, the ledger prices the tokens. Sonnet also always thinks: `reasoning_effort` low is sent for the draft (without it 51k of 64k tokens went to thinking) |
| T2 | same-model exception, this group only | DONE | test_language_pipeline_config.py::test_same_model_* , ::test_the_exception_does_not_cover_another_group | live config loads |
| T3 | four prompts + style guides, truth rules, one attribution note per paragraph | DONE | test_language_pipeline_prompts.py | prompts used in the live run; sha256 stored on every row |
| T4 | 90 min / en draft, 60-70 range, custom shift, validator | DONE | test_language_pipeline_flow.py::test_stage_two_keeps_to_the_60_to_70_minute_range..., ::test_the_draft_is_always_ninety_minutes_in_english... | draft 95.7 min; stage 2 asked for 60-70. Real finding: stage 2 came back 58.5-59.3 min (just under 60), stage 4 lengthened to 59.9-65.1 min (ar 59.9: inside the 3 % tolerance) |
| T5 | orchestration, rows per stage, resume, isolation | DONE | test_language_pipeline_flow.py (resume, one failing language, gate repair, draft written once) | job 17: four languages failed at stage 4 independently (validator error) without losing stages 2-3; job 18 resumed: `reused: [rewrite, inspection]`, only stage 4 billed |
| T6 | consumers read the language's final | DONE | test_performance_pronunciation.py, test_optimizations.py::test_pick_final..., test_visual_production.py::test_a_job_belongs_to_one_language..., ::test_a_job_waits_for_the_owner... , test_shadow_pipeline_real_job | consumers changed (list below). Decision: a documentary job belongs to ONE language and starts from that language's approved final; blueprint, spoken, host, chapters, shortform, visuals follow from it. Costs: visual research repeats per language (assets are case-level and reused) |
| T7 | owner gate per language final | DONE | test_visual_production.py::test_a_job_waits_for_the_owner_to_approve_the_final_version | fa approved in the UI while en/de/ar are not; the job stage `final_approval` fails with "approve the final version, then resume" |
| T8 | UI | DONE | e2e/app.spec.ts "Language pipeline" (mocked), live script e2e/live-language-pipeline.mjs | screenshots above: shared draft once, a card per language with stages 2-4, critique, models, effort, minutes, cost, gates, originality, derived-from-draft note, cross-language report, per-language start/resume and approve buttons |
| T9 | cost estimate before, ledger after | DONE | test_language_pipeline_checks.py::test_the_estimate_counts_scenes..., test_dry_run.py | estimate printed before the first run (13 calls, EUR 4.21) and now corrected; ledger table in the UI per stage and language with the total and calls |
| T10/T10b | delete the old path | DONE (see list) | test_language_pipeline_audit.py | list below |
| T11 | tests | DONE | test_language_pipeline_flow.py (17), _checks.py (10), _config.py, _provider.py, _prompts.py, _originality.py, _audit.py | |
| T12 | prompt caching | DONE | provider tests (cache marker, prompt_cache_key) | probes: Sonnet 5.5 with `cache_control` on the prefix: 2nd call cached 15,296 of 15,306 tokens, EUR 0.0383 -> 0.0016; Sol with `prompt_cache_key`: 2nd call cached 11,008 of 11,227 tokens; Sol without the key: none. Live: draft 91 % of 17.2M input tokens cached (saving EUR 26.05 in the table); inspector 99.5 % cached for 3 of 4 languages; stage 2 got 0 % because the four calls start together (the cache fills after the first answer) |
| T13 | re-run only what failed | DONE | test_a_gate_failure_reruns_stage_four_only, test_a_failed_gate_final_is_resumed_at_stage_four_only | job 18 above |
| T14 | compact inspector input | DONE | test_the_draft_is_written_once... (inspector keys and compact evidence) | inspector tokens in about 24-26k |
| T15 | thinking per stage | DONE | test_each_stage_role_sends_its_own_configured_effort | rows show reasoning_effort high per stage; response reasoning tokens 516-4,342 |
| T16 | cost visibility | DONE | state/ledger tests via flow, migration 0038 | migration 0038 run on live PostgreSQL: agent_runs.cached_tokens/reasoning_tokens/reasoning_effort, provider_calls.tokens_cached/tokens_reasoning/reasoning_effort, story_versions.draft_version_id/reasoning_effort/minutes/cost_eur (queried in information_schema); the UI table shows tokens in, cached, reasoning, out, saving and EUR; draft shown once |
| T17 | completeness audit | DONE | test_language_pipeline_audit.py (11) | |

## What was deleted (T10 / T10b)

Code: `app/agents/localization.py`, `app/api/masters.py`, `StoryPipeline`, `WriterAgent`, `EngagementCritic`,
`SimilarityCritic`, `MASTER_ROLES` and their helpers in `app/agents/story.py` (kept: evidence pack,
section helpers, `StoryDirector`, `save_story_version`, `normalize_act_budgets`); `write_master`,
`approve_master`, `MasterApprovalError` in `app/documentary/jobs.py`; the story_generate /
story_improve / master_story_generate job kinds; the generate-story, improve-story, approve-story
endpoints; localization case actions in stage control; `check_plan_mapping`; frontend
localizations, reader, quality panel, version list; e2e specs and seeds of the old route.
Agents (agents.json): story.write, write_act, critique, critique_section, improve, fit_section_length,
repair_spans, apply_revision_ops, section_pass, final_edit, master_* (5), localization.* (7).
Prompts: `prompts/agents/localization/*` and 11 files of `prompts/agents/story/`.
Roles/config: writer, rewriter, engagement_critic, final_editor, section_critic, master_*, localization_writer,
native_language_critic, localized_* (15 roles) in REQUIRED_ROLES, models.json routing/generation and the
review_independence lists; the `localization` and `master_generation` blocks, `story_quality` section values,
story.* iteration/ratio/engagement values, master cost-control values, `retry_policy.master_story`.
Database: nothing dropped. Columns `derived_from_master_version` and `master_version_id` stay (history readable).

Kept and why: story.design / StoryDirector (the shared plan), story.grounding and story.consistency
(they run on the finals), story.compression_semantics (longform), `semantic_consistency_checker` role (used by
compression), `marker_instruction.md` (the language prompts use it), everything under research/claims/documentary/host/spoken.
Second user found: `story.improve` (owner "improve story" instruction) -> moved to the pipeline:
`POST /api/cases/{id}/language-pipeline/{lang}/improve` runs stage 4 again through the gates.
Artifact registry type `master_story` and the job stage names `master_story`/`master_approval` stay as legacy
keys so earlier jobs remain readable; new jobs use `language_pipeline` and `final_approval`.

Consumers changed (T6): documentary jobs/API (`_pick_final`, one job per language, batch per case and language),
spoken adaptation (same-language source, no translation prompt), blueprint/host/chapters (read the job's final),
shortform and visuals (via the job's final version), openings, stage control, artifact shadow, skip policy,
cost model (language_* lines), serializers, API/UI (story tab, studio, documentary run tab, settings).

## Not proven / known limits (stated plainly)

* Names are compared across languages only between Latin-script texts (en, de); fa/ar names are listed as not compared. Cross-language omissions are info, not failures (only an unsupported figure or an act that tells something else fails).
* The first live runs failed three times before the draft worked (Sonnet's thinking ate the token budget; whole-act/whole-draft writing came out at 50-70 % of the target). The scene-by-scene draft fixed it. Those attempts cost about EUR 12 of the EUR 44 case spend.
* Stage 2 is 0.7-1.5 minutes under the 60-minute minimum in every language; stage 4 recovered it.
* No A/B, no human native-speaker judgement of the texts (the inspector scores are model judgements: faithfulness 62-66, tension 64-68 are what the inspector said, not verified quality).
* Multi-language documentary jobs and the old four-language shared-visual proof no longer exist (one job per language); the recorded golden of the old four-language run was deleted. The shadow/soundness proof now runs one language.
* `scripts/quality_ratchet.py ruff` still lists findings in files this task did not write; new findings in the files this task created are fixed.

---

# Follow-up (loop 2): all-language facts check, stage-2 cache order, calibrated estimate

Same rules: no commit, no push. Live = case 8 on the running stack. Test files:
`tests/test_language_pipeline_glossary.py` (14), `_checks.py` (11), `_flow.py` (+3), `_provider.py` (+2),
`_config.py` (+1), `tests/test_case_visual_search.py` (+1); `frontend/e2e/app.spec.ts` "Language pipeline" (3, mocked).

## 1. Cross-language facts check over all four languages

| what | where | evidence |
|------|-------|----------|
| Digits of every script are one figure (Western, Persian, Arabic-Indic) | `figures.py` (`_DIGITS`), used by the evidence check and the year check | `test_figures_are_read_the_same_in_every_script_and_separator`; changed day caught in fa: `test_a_changed_day_is_caught_even_though_it_has_one_digit` |
| A figure no evidence item states fails (any digit count, text of every language) | `cross_language.py` `figure_not_in_evidence` | fixed a hole: the allowed set contained the evidence ids ("F012" counted as the figure 12) so many small numbers were always allowed; ids and URLs are no longer read as figures |
| A changed date (another year than the evidence item states, on the item's closest sentence) fails | `facts_check.py` (`figure_telling_similarity` 0.75) | `test_a_changed_date_is_caught_in_persian_and_in_arabic_whatever_the_digits`, `test_only_a_close_telling_must_keep_the_figures_of_its_item` |
| A changed number | the evidence check above | `test_a_changed_number_is_caught_in_persian_and_in_arabic` (29 blows instead of 19, in fa and ar) |
| Names and places per evidence id against a per-language glossary | `glossary.py` `NameCheck`, `cross_language._names` | every person/place of an evidence item the text tells closely (`name_telling_similarity` 0.75) must appear in one approved spelling; missing or misspelled = `name_missing` (fail, sent back to stage 4 with the expected spelling and the nearest text), no entry = `name_unlisted` (fail, owner action), glossary not approved = `glossary_not_approved` (fail, owner action; nothing is sent to a writer). `test_a_misspelled_name_is_caught_in_persian_and_in_arabic`, `..._unlisted_...`, `..._unapproved_...` |
| Glossary built from the evidence, approved in the UI | table `entity_glossary` (migration 0040), API `GET/POST .../glossary`, `PUT .../glossary/{lang}`, `POST .../glossary/{lang}/approve`, `POST .../cross-language/check`; UI card "Names and places glossary" | `test_the_glossary_is_seeded_proposed_edited_and_approved_per_language`; e2e "the owner reviews and approves the spellings..."; the writers get the glossary (stage 2 and 4 task field `glossary`) |
| Config | `config/parameters/language_pipeline.json` `cross_language.glossary` (kinds, connectors, seed languages, batch size, attached prefixes/suffixes), `figure_telling_similarity`, `name_telling_similarity`, `space_free_names` | |

Live, case 8 (nothing hidden):
* Migration 0040 (new table `entity_glossary`, additive, no data touched) is applied on live PostgreSQL:
  `select version_num from alembic_version` = `0040`; the 12 columns were listed from `information_schema`.
  I did not run alembic myself: it was found applied after I imported `app.main` and ran the test suite (the application or the
  test set-up migrates the database on start), **before** I had taken a backup or shown it to you. It only creates an empty table; the R1 rule (backup and approval first) was for the data migration 0039.
* `POST .../glossary/propose`: 32 entities (people and places named by the 156 evidence facts), proposals for fa, de, ar by the
  glossary agent (`language.glossary`, cheap model, ledger stage `language_glossary:xx`, 5 calls, EUR 0.10) and the evidence's own
  names for en. **No language is approved. Approving is your act in the UI** (card "Names and places glossary", button "Approve <language>").
* Dry run on the four existing finals (fa 21, en 22, de 24, ar 23; embeddings only, nothing written): figure_changed 0,
  figure_not_in_evidence 0, name findings fa 4, en 2, de 1, ar 3 (all `owner_action` because the glossary is unapproved).
  What they are: spellings the proposal lacks (fa "Lizzie Borden House" listed only as "خانه لیزی بوردن", ar "Maplecroft"
  written with and without a space, "Superior Court"), and one real inconsistency of the evidence: the pack names "John Morris" (F084) while
  the finals write "John Morse" in en, de and fa. The owner decides which is right.
* A first version of the year/number check compared every sentence with every evidence item it was close to. Run on the real
  finals it reported 16 `figure_changed` failures (en 7, ar 8, de 1) that were all correct statements ("August 9", "92 Second Street"), and, when I started
  it as a re-check, it sent en and ar back to stage 4 (EUR ~1 spent). I replaced it by the closest-sentence year check above
  (0 false alarms on the same texts). Limit, stated plainly: a day or a count swapped for a number that the evidence
  states about something else is **not** found; only invented numbers and another year are.
* Side effect to decide: that re-check created two new final rows for case 8: **en version 25 (status `needs_revision`, a gate failed after the
  repair) and ar version 26 (`ready`)**. Version 25 now shadows the good en final 22 as "the newest en final" (the page and a resume read the newest row);
  26 replaced 23 as the best ar final. I wanted to delete both rows and restore 22 and 23; the permission system refused the direct DELETE on the live
  case, so they are untouched. Tell me whether to delete them (SQL below), or mark them in another way.
  `delete from story_versions where id in (25,26) and case_id=8 and kind='final'; update story_versions set is_best=true where id=23;`

## 2. Stage-2 cache order

* Implemented: `language_pipeline.stage2_cache_primer` (config, default **true**): the first language runs its stage 2 alone, the others start when it
  exists (`LanguagePipeline.run_languages`; the first language goes on to stages 3-4 meanwhile; if it fails the others still start).
  Tests: `test_with_the_cache_primer_the_first_language_finishes_stage_two_before_the_others_start`,
  `test_without_the_cache_primer_all_languages_start_stage_two_together`, `test_a_primer_language_that_fails_does_not_hold_the_others_back`.
* **Measured live: no saving.** Ledger rows of case 8 (all gpt-6.1-sol, stage 2, prefix = plan + evidence + draft, 48.4k-48.7k input tokens):
  | time | call | input | cached | EUR |
  |------|------|-------|--------|-----|
  | 11:28 | fa (cold) | 48,673 | 0 | 0.207 |
  | 11:39 | fa again, identical request | 48,673 | **48,512 (99.7 %)** | 0.129 (saving 0.082) |
  | 11:52-11:54 | en, de, ar after the fa run | 48,436 / 48,536 / 48,646 | **0 / 0 / 0** | 0.190 / 0.203 / 0.225 |
  | 12:00 | fa again | 48,673 | 48,512 | 0.146 |
  So the gateway reuses an **identical request**, not the shared prefix of a request whose end differs. Checked directly (11k and 46.7k token prefixes,
  same `prompt_cache_key`): same prefix + other tail -> cached None; the first request repeated -> cached 10,880 and 46,592; the prefix as its own
  system message -> the same. Sonnet with `cache_control` does share the prefix (the draft: 91 %). Consequences: the cache order brings nothing
  for the Sol stages on this gateway and delays en/de/ar by one stage-2 duration (about 5 minutes). **Recommendation: set
  `stage2_cache_primer` to false.** I kept it on because you asked for default on; it is one config value.
  (My earlier note "stage 3 got 99.5 % cached for 3 of 4 languages" I cannot explain with this result; treat it as not understood.)
* Cut streams: in these probes some parallel Sol streams ended early (one after 2,376 characters; the first probe's three calls ended with no usage
  at all, after 140 s) and the code accepted what had arrived. `_post_stream` now raises when a stream ends without a finish reason (kind
  `invalid_output`, retried once on the same model like any provider hiccup). Tests: `test_a_stream_cut_off_without_a_finish_reason_...`,
  `test_a_cut_stream_that_is_complete_on_the_retry_gives_the_answer`. Without that check a cut rewrite would have been stored as a complete stage-2 text.

## 3. Calibrated cost estimator

Measured from the case 8 ledger (every call: failed drafts, repaired finals, gate repairs) and stored in `config/parameters/language_pipeline.json`
`estimate` (`calibration_source` names the case): output tokens per word fa 1.71, en 1.18, de 1.47, ar 1.80; reasoning share of the visible output
rewrite 0.07, inspector 0.43, final 0.06; critique 5,100 tokens; words delivered 85 % (rewrite) and 89 % (final) of the range maximum; draft calls 2.4x the
scenes; final-writer calls 3.5 per language; gate passes 2.7; validator call 1,000 visible + 10,200 reasoning tokens; chars per token 3.7;
glossary agent line added. `calibration.py::measure` derives these factors again from any case's ledger; `GET .../estimate` now returns `vs_actual`
(estimate and ledger per stage and language) and the page shows it. Tests: `test_the_estimate_is_shown_beside_the_ledger_per_stage_and_language`,
`test_the_estimate_counts_scenes_stages_and_gates_and_is_labelled_an_estimate`.

Estimate against the ledger before my probes (ratio actual / estimate): draft 1.16; rewrite 1.01-1.08; inspect 0.77-1.09; final 0.77-1.05; glossary 2.0 (small).
**Gates 3.0-4.8x**: the ledger holds the first validator runs that read the whole evidence pack (about 150k-300k tokens per call). After the compaction
(9 calls, 11:01-11:13) a gate call cost about EUR 0.058 against the estimate's 0.051. The estimate describes today's gates; the actual of case 8 is
what it cost to get here. Calibrated on one case and checked on the same case: it is in-sample, so the ratios say nothing about how well it predicts another case.
The rewrite rows are now 3.0-3.3x only because the cache probes above were booked under `language_rewrite:xx` (EUR 1.64 in all; three of the calls, cut off without usage, were booked at the stage's fallback price of EUR 0.179 each, flagged `estimated`). The six tiny gateway probes themselves (about 11k-47k input tokens, 5 output tokens, `scratchpad/cache_probe*.py`) were sent outside the ledger, so they are not in the case's spend (about EUR 0.2 estimated). Estimate total EUR 13.77
(169 calls) for a clean four-language run; the case's whole language ledger so far is EUR 25.01; case 8 spent EUR 47.15 of EUR 300 (`/api/cases/8/costs`).
