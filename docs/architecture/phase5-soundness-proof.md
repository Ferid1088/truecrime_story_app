# Phase 5 snapshot-diff proof — 9F Step 3

Command: `PYTHONPATH=.:tests .venv/bin/python tests/run_soundness_phase5.py`.

Evidence parent: `/private/var/folders/4t/4k_v5bnj1ng81bs7wj7g3sgw0000gn/T/tc-soundness-phase5-d24rteis`.

The template was rebuilt automatically from the stale manifest; its baseline run took 40.898 s. Each case used a copied SQLite database and output directory. Total perturbation runtime was 1133.914 s; restore times were 0.000–0.937 s.

Actual sets are snapshot-hash differences from `soundness_observation.observe`; predicted sets are registry downstream closures. `hidden` is actual minus predicted. The complete sets are retained in `tests/golden/soundness_phase5_results.json`.

| perturbation | status | actual count | predicted count | over-invalidation count | hidden inputs |
|---|---|---:|---:|---:|---|
| agent_model_routing | passed | 26 | 60 | 34 | none |
| channel_identity_logo | passed | 26 | 60 | 34 | none |
| host_plan | passed | 1 | 13 | 12 | none |
| intro_design | passed | 3 | 60 | 57 | none |
| language_profile | passed | 26 | 60 | 34 | none |
| logo_bytes | passed | 26 | 60 | 34 | none |
| master_story | passed | 14 | 56 | 42 | none |
| music_history_library | passed | 4 | 60 | 56 | none |
| naming | passed | 7 | 13 | 6 | none |
| one_beat | passed | 6 | 49 | 43 | none |
| one_voice_take | passed | 1 | 1 | 0 | none |
| pronunciation_voice | passed | 26 | 60 | 34 | none |
| research_result | unproven | 0 | 0 | 0 | none |
| typed_config_audio | passed | 26 | 60 | 34 | none |
| typed_config_chapters | passed | 26 | 60 | 34 | none |
| typed_config_documentary | passed | 26 | 60 | 34 | none |
| typed_config_host | passed | 26 | 60 | 34 | none |
| typed_config_music | passed | 35 | 60 | 25 | none |
| typed_config_render | passed | 26 | 60 | 34 | none |
| typed_config_visual | passed | 26 | 60 | 34 | none |
| typed_config_voice | passed | 34 | 60 | 26 | none |
| unused_visual | passed | 3 | 12 | 9 | none |

The `research_result` perturbation is UNPROVEN, not passed: the restored fixture is a continuation job (`documentary_jobs.stages_json` has no `research` stage) and `research_results` has zero rows. No registry root or synthetic research edit was introduced.

All implementable perturbations completed with `hidden = []`. The prior hidden input from typed music configuration was fixed before this run by registering `music_cues:shared` for unconsumed music-library tracks; the focused regression command was `PYTHONPATH=. .venv/bin/pytest -q tests/test_artifact_shadow.py tests/test_artifact_registry.py tests/test_artifact_input_coverage.py` with `20 passed`.

The full predicted/actual/over-invalidation sets for every perturbation are machine-readable in the committed result record.

