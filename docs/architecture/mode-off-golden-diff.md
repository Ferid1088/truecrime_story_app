# Full-documentary mode-off golden

The pre-incremental full-documentary golden was captured in a disposable
worktree at ref `05320d5` (`Add artifact soundness snapshot harness`) by
running `tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job`
with no network providers and hashing the saved pipeline rows and output files.
The committed golden is
`tests/golden/documentary_pipeline_pre_incremental.json`.

The current mode-off run is compared by snapshot key in
`tests/test_visual_production.py:869-891` (the comparison is not a registry
hash comparison). The exact scope counts are:

| snapshot scope | changed keys | explanation |
|---|---:|---|
| `audio_plans` | 1 | expected: Phase 2 generation response carries the complete request hash metadata |
| `cases` | 1 | expected: current mode-off run has the current pipeline status/output metadata |
| `chapter_plans` | 1 | expected: chapter writer response is request-sensitive |
| `host_scenes` | 8 | expected: current host generation identifiers/content reflect request-sensitive generation |
| `media_usages` | 16 | expected: current output records use the current request-sensitive visual/music outputs |
| `music_tracks` | 10 | expected: Phase 2 music fake changes valid tone bytes from the complete request hash |
| `music_usages` | 2 | expected: usage rows point at the current request-sensitive track outputs |
| `production_scripts` | 8 | expected: downstream scripts contain changed voice/visual/LLM outputs |
| `story_versions` | 4 | expected: spoken/LLM text includes the request hash marker |
| `visual_assets` | 7 | expected: visual-search metadata includes the complete request hash |
| `visual_audits` | 3 | expected: audit records carry the current generation response metadata |
| `visual_plans` | 1 | expected: plan consumes the current request-sensitive visual output |
| `voice_performances` | 5 | expected: voice output metadata is derived from request-sensitive takes |
| `videos` | 8 | expected: rendered-file identities differ after valid request-sensitive media changes |
| files | 86 | expected: valid TTS/music/render bytes differ after Phase 2 request hashing |

The run reported `different=161`, with no missing or new snapshot keys. These
are behavior changes intentionally introduced by Phase 2; no `SOUND` status is
derived from this golden comparison.
