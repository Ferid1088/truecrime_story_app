# Phase 4 checkpoint — language-isolation blocker

This is a retained failing proof, not a SOUND record. The unchanged cold
replay passed with zero actual changes after all 44 stage functions executed.
The used-visual perturbation also had no hidden input. Phase 4 cannot be
completed under the requested language-isolation contract because the current
pipeline has a shared chapter request that consumes all localized narration.

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

The exact consumer is `app/agents/chapters.py:44-59` (collects every spoken
version's first 1,500 characters), serialized in `_payload` at
`app/agents/chapters.py:62-78`, and called through `_write` at
`app/agents/chapters.py:119-120`. The final run also showed the naming case's
visual-verification dependency; that real edge was added to
`app/core/artifact_dependencies.py:58` and the rerun no longer reported it as
hidden. The previous intro discrepancy was an observation-label error: script
intro placement belongs to production-script output, not reusable intro media.

Fixing the remaining blocker requires one of two behavior changes: remove
localized excerpts from the chapter request, or split `chapter_plan`/cards into
language-scoped artifacts. Neither is authorized by the current proof request,
so no workaround is claimed. `config/soundness_status.json` remains entirely
UNPROVEN. Phase 5, Phase 6, and R were not started.
