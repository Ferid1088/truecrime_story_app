# Hash soundness matrix

This table is the committed output contract for the snapshot-diff soundness
harness. The existing real four-language smoke test is exercised by
`tests/test_visual_production.py:638`; the non-circular saved-output helper is
in `app/services/soundness.py:15`. Output comparisons use
the named `SNAPSHOT_EXCLUDED_FIELDS` constant in
`app/services/soundness.py:11` (generated member IDs and timestamps), while
input fingerprints still treat generated IDs as real inputs.

| perturbation | predicted changed set | actual changed set | hidden inputs | over-invalidation | status |
|---|---|---|---|---|---|
| research result | research_result → master_story → downstream closure | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| master story text | master_story → downstream closure | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| one beat | beats → downstream closure | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| one spoken version (one language) | spoken_version[language] → same-language closure | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| one voice take | voice_takes[language] → same-language timing closure | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| one visual asset | asset → using shots/pieces/plan closure | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| host plan | host_plan → host_segments → production/render | registry graph | not independently rerun | conservative config inputs | UNPROVEN |
| naming title | naming → production_script → final_render | dependency test | not independently rerun | metadata consumers outside graph | UNPROVEN |
| each typed config section | all registered artifact input hashes | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| language profile | per-language branch | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| pronunciation/voice setting | spoken/voice/timing/captions/render | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| channel identity/logo | intro → final_render | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| intro design version | intro → final_render | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| agent definitions/model routing | AI-produced artifacts | not run in 9B | not evaluated | not evaluated | UNPROVEN |
| music history/library | music_cues → production/render | not run in 9B | not evaluated | not evaluated | UNPROVEN |

No type is currently `SOUND`. A type may be promoted only by a full
input-sensitive-provider run whose actual set comes from `artifact_snapshot`,
not from registry hashes or the closure used for prediction. The existing
registry-only test is retained as a graph sanity check, not as soundness
evidence.

## Per-type gate

All registered types are `UNPROVEN` in `config/soundness_status.json`.
Enforce mode refuses every type with `INCREMENTAL_UNPROVEN`; the default
policy remains `mode: off`.
