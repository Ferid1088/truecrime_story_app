# Phase 4 checkpoint — language-isolation blocker

This is a retained failing proof, not a SOUND record. The unchanged cold
replay passed with zero actual changes after all 44 stage functions executed.
The used-visual perturbation also had no hidden input. Before the 9F Step 1
fix, Phase 4 could not be completed under the requested language-isolation
contract because the pipeline had a shared chapter request that consumed all
localized narration.

Evidence run directory:

`/private/var/folders/4t/4k_v5bnj1ng81bs7wj7g3sgw0000gn/T/tc-soundness-phase4-dy_e32ne`

The final rerun used the post-label-fix harness and retained raw `before.json`,
`after.json`, `result.json`, stage traces, and logs. Its German spoken-version
result was:

| artifact | predicted | actual | hidden / isolation |
|---|---:|---:|---|
| spoken_version:de | yes | yes | — |
| voice_takes:de | yes | yes | — |
| audio_timing:de | yes | yes | — |
| host_segments:de | yes | yes | — |
| music_cues:de | yes | yes | — |
| captions:de | yes | yes | — |
| production_script:de | yes | yes | — |
| final_render:de | yes | yes | — |
| chapter_plan:shared | no | yes | hidden input; cross-language |
| chapter_cards:en/fa/ar/de | no | yes | hidden input; cross-language |
| production_script:en/fa/ar | no | yes | hidden input; cross-language |
| final_render:en/fa/ar | no | yes | hidden input; cross-language |

The exact former consumer was `app/agents/chapters.py:44-59` (it collected
every spoken version's first 1,500 characters), serialized in `_payload` at
`app/agents/chapters.py:62-78`, and called through `_write` at
`app/agents/chapters.py:119-120`. Step 1 of 9F now passes one language's
excerpt to one writer/auditor loop at `app/agents/chapters.py:35-71,119-170`;
the shared plan is neutral. The final run also showed the naming case's
visual-verification dependency; that real edge was added to
`app/core/artifact_dependencies.py:58` and the rerun no longer reported it as
hidden. The previous intro discrepancy was an observation-label error: script
intro placement belongs to production-script output, not reusable intro media.

The retained table above remains the pre-fix regression evidence. The new
regression test is
`tests/test_soundness_observation.py:test_german_card_edit_does_not_change_shared_or_other_language_observation`:
after the fix it reports only `chapter_cards:de` for a German card edit.
`config/soundness_status.json` remains entirely UNPROVEN until the rerun of
Phases 4–6 supplies passing snapshot-diff records.
