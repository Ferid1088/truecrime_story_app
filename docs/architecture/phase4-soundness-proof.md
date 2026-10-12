# Phase 4 snapshot-diff proof — 9F Step 2

Commit under test: `25cf3aabc469e78dac0dc678241f74c1f4fea5c4`.

Command:

```text
PYTHONPATH=.:tests .venv/bin/python tests/run_soundness_phase4.py
```

The persistent SQLite template was rebuilt because its manifest was stale.
The template baseline run took 41.878 s. The unchanged control restored in
0.175 s and took 41.239 s; perturbation restores took 0.238–0.400 s. Raw
evidence is retained at
`/private/var/folders/4t/4k_v5bnj1ng81bs7wj7g3sgw0000gn/T/tc-soundness-phase4-6cxnt681`.

The control had `actual_changed = []`. For every perturbation,
`actual_changed` came from `soundness_observation.observe` snapshot hashes,
while `predicted_changed` came from the registry downstream closure. All
three had `hidden = []`; the German perturbation had no non-German actual
artifact changes.

## German spoken version

| artifact type | language | predicted? | actual? |
|---|---|---:|---:|
| spoken_version | de | yes | yes |
| voice_takes | de | yes | yes |
| audio_timing | de | yes | yes |
| host_segments | de | yes | yes |
| music_cues | de | yes | yes |
| captions | de | yes | yes |
| chapter_cards | de | yes | yes |
| production_script | de | yes | yes |
| final_render | de | yes | yes |
| film_length | de | yes | no |

Over-invalidation: `film_length:de`.

## Naming/title

| artifact type | language | predicted? | actual? |
|---|---|---:|---:|
| naming | shared | yes | yes |
| visual_asset | shared | yes | yes |
| visual_pieces | shared | yes | no |
| visual_plan | shared | yes | no |
| visual_shots | shared | yes | no |
| production_script | en/de/fa/ar | yes | no |
| final_render | en/de/fa/ar | yes | yes |

Over-invalidation: `visual_pieces:shared`, `visual_plan:shared`,
`visual_shots:shared`, and `production_script:en/de/fa/ar`.

## Used visual asset

| artifact type | language | predicted? | actual? |
|---|---|---:|---:|
| visual_asset | shared | yes | yes |
| visual_pieces | shared | yes | no |
| visual_plan | shared | yes | no |
| visual_shots | shared | yes | no |
| production_script | en/de/fa/ar | yes | no |
| final_render | en/de/fa/ar | yes | yes |

Over-invalidation: `visual_pieces:shared`, `visual_plan:shared`,
`visual_shots:shared`, and `production_script:en/de/fa/ar`.

