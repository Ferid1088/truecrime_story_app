# 9H provider-payload boundary

At Part 2, the fakes were changed to fingerprint the adapter request after
request construction, rather than a broad typed configuration object.

| fake | fake call site | production request builder | request now fingerprinted |
|---|---|---|---|
| generation | `tests/test_visual_production.py:648-672` | `app/providers/generation/openai_compat.py:168-188` | model, temperature, max_tokens, messages, images, optional seed |
| host generation | `tests/test_host.py:181-194` | same generation adapter | the same OpenAI-compatible body |
| TTS | `tests/test_voice_render.py:54-65` | `app/providers/voice/elevenlabs.py:43-58,63-72` | voice path id, output-format query, and JSON body |
| local ASR | `tests/test_voice_render.py:84-104` | `app/documentary/asr.py:324-333` | audio bytes, language, model/compute type, fixed timestamp/VAD options |
| music | `tests/test_spoken_audio.py:517-527` | `app/providers/voice/elevenlabs.py:181-191` | output-format query and sound-generation JSON body |
| research | `tests/test_visual_production.py:915-923` | research-provider method boundary | complete method arguments |
| visual search | `tests/test_visual_production.py:1116-1133` | `app/documentary/visuals/research.py:126-155,187-201` | case/query/found-during/options arguments |
| maps | `tests/test_visual_production.py:1157-1188` | `app/documentary/visuals/maps.py:249-323` | geocode/render method arguments |

The removed `config_hash_payload(ai_config...)` calls were not real provider
requests. In particular, `production_policies.incremental` is a reuse policy,
not generation input. The test-only request recorder remains at
`tests/mock_payload.py:69-97`; it records the resulting request object without
secrets.

## Preserved earlier experiment

Before this commit, an uncommitted 9G experiment narrowed only generation and
host hashes. It was not discarded under the 9H rule. On base `15061ec`, a
logo-byte replay unexpectedly changed
`audio_timing:{ar,de,en,fa}`, `music_cues:{ar,de,en,fa}`, `music_cues:shared`,
`production_script:{ar,de,en,fa}`, and `visual_asset:shared`, while the payload
recorder carried no logo field into generation/TTS/ASR/search/music requests.
That is a local-code/cache or fixture finding, not evidence of provider
config. It is retained here for Part 2 follow-up rather than being silently
treated as noise.

## Golden update

The old golden was produced with the broad fake payload. Applying the request-
boundary change altered 110 saved-output snapshot keys: 31 persisted database
rows (chapter/host/story/visual/voice/production rows) and 79 audio,
production JSON, render, and library files. These are all downstream outputs
whose fake provider bytes or request identifiers contain the changed hash; no
production provider request gained a config field. The refreshed golden is
`tests/golden/documentary_pipeline_pre_incremental.json`. The real full-job
mode-off assertion passed after replacement:

```text
1 passed in 62.31s
MODE_OFF_GOLDEN_DIFF {"different": 0, ...}
```

## Persistent-template finding

The first config replay batch also found that SQLite `sqlite_sequence` values
survived derived-row deletion from a copied template. Regenerated member IDs
then changed every production script and made logo/config effects appear real.
`tests/soundness_replay.py` now resets the disposable run's sequence table after
derived rows are deleted; this is harness canonicalization, not a pipeline
behavior change. The full batch is rerun after this fix.

## Final Part-2 findings

The isolated replay of `typed_config_music` after the template rebuild produced
the following snapshot-only result:

```text
actual: audio_timing:{ar,de,en,fa}, final_render:{ar,de,en,fa},
       music_cues:{ar,de,en,fa}, music_cues:shared
hidden before declaration: audio_timing:{ar,de,en,fa}
```

Payload logs show the TTS and ASR request payloads are unchanged by this
perturbation. The changed fields are the music placement path and library file
hash in each language's persisted `manifest.json`. The observer assigns that
whole manifest to `audio_timing` at `tests/soundness_observation.py:122-125`,
while the music producer writes those fields at `app/documentary/music.py:790`
and `app/documentary/music.py:833-845`. This is therefore a declared local
output effect, not a mock artifact: `audio_timing.config_inputs` now includes
`music` and `music_history_library` in `app/core/artifact_dependencies.py:99-104`.
The shared unconsumed library row is explicitly predicted as
`music_cues:shared` by `predicted_for_config()` at
`app/core/artifact_dependencies.py:134-141`.

The complete concurrent batch that preceded this final declaration preserved
its evidence in the temporary replay directory and recorded two findings: an
old logo replay's production-script deltas were path/member-ID artifacts, and
the typed-music replay exposed the manifest ownership edge above. No artifact
type is promoted to SOUND by this checkpoint.

## Full-batch checkpoint and isolation result

The rerun after isolating `intro_dir` completed 18 perturbations in 332.669 s
(`tests/run_soundness_phase5.py`; evidence directory
`/private/var/folders/4t/4k_v5bnj1ng81bs7wj7g3sgw0000gn/T/tc-soundness-phase5-gpgwvfkk`).
All records had `hidden: []` except `typed_config_render`, whose four
`intro:*` artifacts were the direct local effect of render dimensions/fps.
After declaring `render` for `intro`, the isolated replay reported:

```text
typed_config_render
predicted = actual = final_render:{ar,de,en,fa}, intro:{ar,de,en,fa},
             production_script:{ar,de,en,fa}
hidden = []
```

The full batch also confirmed the logo perturbation (only the English logo was
changed) changed `intro:en` and `final_render:en`; no provider request carried
logo bytes. The mode-off regression after the run-root/intro isolation passed
`1 passed in 61.54s`; the refreshed golden contains 193 snapshot keys and the
comparison is zero-diff.
