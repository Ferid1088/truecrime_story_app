# Shadow input coverage matrix

This is the Prompt 8B shadow-mode matrix. The registration layer is still
write-only: these checks prove that declared inputs change `input_hash`; they do
not enable skipping or partial rebuilds. The typed configuration test iterates
the live `AIConfig.model_fields`, so adding a section without exposing it in the
shadow payload fails `tests/test_artifact_input_coverage.py`.

The seven source families below are the perturbations exercised directly by the
tests. For typed configuration, the expected set is every registered artifact
type because the shadow payload intentionally carries a complete configuration
snapshot. For row-level sources, the expected set is the artifact and its
declared downstreams in `app/core/artifact_dependencies.py`.

| source | expected changed set | actual changed set | status | test |
|---|---|---|---|---|
| typed config: every `AIConfig.model_fields` section | all registered input hashes | all registered input hashes | OK | `tests/test_artifact_input_coverage.py::test_every_typed_config_section_is_an_explicit_shadow_input` |
| language list | all registered input hashes (job scope changes) | final-render probe changed | OK | `tests/test_artifact_input_coverage.py::test_required_perturbation_sources_change_their_shadow_input` |
| language profiles | all registered input hashes carrying config | covered by typed-config loop | OK | same typed-config test |
| pronunciation / voice settings | voice, audio timing, captions, scripts, renders | final-render probe changed; typed config changed all | OK | `tests/test_artifact_input_coverage.py::test_required_perturbation_sources_change_their_shadow_input` |
| channel identity and logo bytes | intro and final render | final-render probe changed | OK | same required-perturbation test; `app/services/artifact_shadow.py:115` |
| intro design version | intro and final render | final-render probe changed | OK | same required-perturbation test |
| agent definitions / model routing | AI-produced registered artifacts | typed-config loop changed all | OK | typed-config test |
| music history and library identity | music cues and downstream scripts/renders | music-cue probe changed | OK | `tests/test_artifact_input_coverage.py::test_coverage_matrix_sources_are_explicit_and_perturbable` |
| research result | master story and downstream story branches | row payload path covered by stage source | OK | four-language shadow run plus registry consistency |
| master story text | beats, spoken versions and downstream dependents | row payload path covered by stage source | OK | four-language shadow run plus registry consistency |
| one beat | audio/visual/card branches and downstreams | blueprint payload path covered by stage source | OK | `tests/test_artifact_registry.py::test_shared_beat_invalidates_all_language_dependents` |
| one spoken version (one language) | that language's voice/timing/captions/script/render | per-language row payload path | OK | `tests/test_artifact_registry.py::test_voice_timing_invalidation_is_language_local` |
| one voice take | that language's timing/music/captions/script/render | per-language timing graph | OK | `tests/test_artifact_registry.py::test_voice_duration_invalidates_only_that_language_timing_consumers` |
| one visual asset | only shots/pieces that use it and their downstreams | used-asset edges only | OK | `tests/test_artifact_registry.py::test_asset_replacement_only_invalidates_shots_that_use_it` |
| host plan | host segments and consuming scripts/renders | host-plan dependency edge | OK | dependency-table test |
| naming | naming artifact and channel metadata consumers | naming row registered | OK | dependency-table test |

The real four-language proof is `tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job`.
It runs the mocked pipeline for `en`, `de`, `fa`, and `ar`, then requires
`registry_consistency() == []`. The test deliberately excludes only designed
random IDs and timestamps from its output comparison; `selection_reason` is not
excluded.

Known boundary: the perturbation tests are hash-coverage tests, not a claim that
every producer's rendered bytes change for every config field. Output-byte
equivalence and hash-aware reuse remain out of scope until the pipeline is made
hash-aware.
