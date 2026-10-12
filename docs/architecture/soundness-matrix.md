# Hash soundness matrix

This table is the committed output contract for the read-only soundness
harness. The real four-language baseline is exercised by
`tests/test_visual_production.py:638`; the registry-level perturbation
assertion is in `tests/test_soundness_matrix.py:18`. The only excluded fields
are the named `FINGERPRINT_VOLATILE_FIELDS` constant in
`app/core/fingerprint.py:18` (timestamps and run metadata); generated member
IDs are not silently excluded from this contract.

| perturbation | predicted changed set | actual changed set | hidden inputs | over-invalidation | status |
|---|---|---|---|---|---|
| research result | research_result → master_story → downstream closure | harness closure | none | downstream closure may be conservative | SOUND |
| master story text | master_story → downstream closure | harness closure | none | downstream closure may be conservative | SOUND |
| one beat | beats → downstream closure | harness closure | none | downstream closure may be conservative | SOUND |
| one spoken version (one language) | spoken_version[language] → same-language closure | full-pipeline test covers language isolation | none observed | conservative config inputs | SOUND |
| one voice take | voice_takes[language] → same-language timing closure | full-pipeline test covers language isolation | none observed | conservative config inputs | SOUND |
| one visual asset | asset → using shots/pieces/plan closure | asset test covers used-asset edges | none observed | unused assets are not downstream | SOUND |
| host plan | host_plan → host_segments → production/render | registry graph | not independently rerun | conservative config inputs | UNPROVEN |
| naming title | naming → production_script → final_render | dependency test | not independently rerun | metadata consumers outside graph | UNPROVEN |
| each typed config section | all registered artifact input hashes | hash perturbation matrix | none in hash-level harness | all types are conservative | SOUND |
| language profile | per-language branch | full four-language baseline | not independently rerun | conservative config inputs | UNPROVEN |
| pronunciation/voice setting | spoken/voice/timing/captions/render | hash perturbation matrix | none in hash-level harness | config snapshot is broad | SOUND |
| channel identity/logo | intro → final_render | hash perturbation matrix | none in hash-level harness | config snapshot is broad | SOUND |
| intro design version | intro → final_render | hash perturbation matrix | none in hash-level harness | config snapshot is broad | SOUND |
| agent definitions/model routing | AI-produced artifacts | hash perturbation matrix | none in hash-level harness | config snapshot is broad | SOUND |
| music history/library | music_cues → production/render | existing music fingerprint test | none observed | conservative config inputs | SOUND |

`SOUND` means the checked changed set had no actual artifact outside the
predicted closure. `UNPROVEN` is intentional: those rows need a full
perturb-and-rerun proof before enforce mode may use them. The current test does
not claim that a hash-only perturbation is a substitute for every full-pipeline
rerun; this limitation is tracked rather than hidden.

## Per-type gate

SOUND: `research_result`, `master_story`, `beats`, `audio_blocks`,
`spoken_version`, `voice_takes`, `audio_timing`, `visual_needs`,
`visual_asset`, `visual_shots`, `visual_pieces`, `visual_plan`, `chapter_plan`,
`chapter_cards`, `timeline_cards`, `intro`, `music_cues`, `captions`,
`film_length`.

UNPROVEN: `host_plan`, `host_segments`, `naming`, `production_script`,
`final_render`. Enforce mode refuses these types with
`INCREMENTAL_UNPROVEN`; the default policy remains `mode: off`.
