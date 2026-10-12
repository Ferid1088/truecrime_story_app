# 9K Part 2 — no-effect evidence triage

The regenerated matrix contains 24 artifact types. Its reach/effect columns
contain 166 `none` (N) and 29 `weak` (W) type/perturbation pairs; 44 are
strong. The registry's predicted closure is not treated as proof of a saved
output change.

## Classification of observed no-effect edges

| pair group | class | evidence and disposition |
|---|---|---|
| `agent_model_routing → beats, host_plan, master_story, naming` | ii — field not consumed | `tests/soundness_replay.py` changes only `routing["chapter_writer"]`; the request embeds the model for the producer's own role (`tests/mock_payload.py:126-133`, `config/agents.json`). The roles differ, so the changed field is not consumed. Kept as a cited no-effect edge. |
| `typed_config_documentary → beats, host_plan, master_story, naming, production_script` | ii — field not consumed in this fixture | `app/documentary/jobs.py:238` uses `ai_config.documentary.pilot_seconds` only when `job.pilot_seconds` is false; the fixture supplies `pilot_seconds=80.0` (`tests/test_visual_production.py:1406-1407`). Kept as a fixture-scoped no-effect reason, not promoted to SOUND. |
| `master_story → naming` | ii — field not consumed | `app/naming/context.py:54-103` reads case identity and blueprint, not the perturbed master prose. |
| `master_story → chapter_plan` | ii — field not consumed | `app/agents/chapters.py:45-67` receives acts/events; `app/documentary/chapters.py:69-89` derives those from plan metadata, while the perturbation changes `sections` prose. |
| `one_beat → chapter_plan, timeline_cards` | ii — field not consumed | `app/documentary/chapters.py:87-88` gates beat summaries by firewall purpose, and `chapters.py:49,99-108` reads only `reveals`, `relies_on`, and `viewer_knows`. The fixture's B01 purpose is `hook`, so these changed fields do not enter the request. |
| `unused_visual → visual_pieces, visual_shots` | ii — field not consumed | The changed asset is not referenced by a saved production-script shot; the committed replay records that exact condition. |
| `one_beat → audio_blocks, host_plan, visual_needs`; `host_plan → host_segments:*`; `one_voice_take → audio_timing:de, production_script:de`; `unused_visual_metadata → visual_pieces, visual_shots` | unresolved backlog | The current records have only generic no-effect text for these pairs. They remain UNPROVEN. The next replay must inject the intermediate artifact and run only its downstream closure; no SOUND promotion was made. |

No class-iii over-declared edge was evidenced by the committed read-recording
proxy or consumer source inspection, so no dependency/config edge was removed.
Removing one would be an unsupported behavior change. No hidden input was
found (`hidden=[]` in every committed Phase 5 record).

## Harness before/after

The concrete harness defect found in this bounded pass was master reseeding:
before, `SOUNDNESS_FROM_ZERO` reseeded the master after every research step;
after, `SOUNDNESS_SEED_MASTER=1` is required and is set only for the initial
template build. The real rerun now preserves an intermediate master edit and
reports `master_story: run` with both hash changes. The analogous
`one_beat`, `spoken_de`, `one_voice_take`, and `host_plan` downstream-only
replays were not implemented in this bounded pass; their exact remaining
evidence gaps are retained above and in the generated matrix.

## Final status

SOUND: `master_story`, `naming`, `research_result`.

UNPROVEN: `audio_blocks`, `audio_timing`, `beats`, `captions`, `chapter_cards`,
`chapter_plan`, `film_length`, `final_render`, `host_plan`, `host_segments`,
`intro`, `music_cues`, `production_script`, `spoken_version`, `timeline_cards`,
`visual_asset`, `visual_needs`, `visual_pieces`, `visual_plan`, `visual_shots`,
`voice_takes`.

The exact missing perturbation/evidence text for every UNPROVEN type is the
generated table in `docs/architecture/soundness-matrix.md`; the machine-readable
copy is `config/soundness_status.json`. Both were regenerated with
`tests/generate_soundness_status.py` and remained byte-identical because this
bounded pass added no new passing snapshot-diff record.
