# Config reads and dead-config candidates

Prompt 9I Part 2 used the transparent proxy at
`tests/config_read_recorder.py:9-57` inside the real forced four-language
pipeline (`tests/soundness_replay.py:500-511`). Each of the four zero-effect
config runs emitted 190 reads to its independent `config_reads.json`. The
committed compact union is `tests/golden/config_read_records.json`.

The fields changed by the zero-effect perturbations were read but did not
change a saved output in this fixture: `audio_direction.music_lead_seconds`,
`documentary.pilot_seconds`, `visual_search.max_candidates_per_query`, and
`voice.between_blocks_ms`. They are therefore UNPROVEN effects, not SOUND
evidence and not dead-config evidence.

Candidates not read by any of those four complete runs are:

* `audio_direction`: `max_repair_iterations`, `music_tail_seconds`,
  `room_tone_level_db`, `sting_level_db`.
* `documentary`: `languages`, `speech_wpm` (the run read the `wpm` helper,
  which is a derived access rather than a field name).
* `visual_search`: `blocked_domains`, `max_assets`, `max_download_mb`,
  `max_source_pages`, `min_request_interval_s`, `min_width`, `providers`,
  `request_timeout_s`, `user_agent`, `wikimedia_api`.
* `voice`: `between_sections_ms`, `provider`.
* `pronunciation`: `max_rounds`, `max_vowel_distance`, `pad_seconds`,
  `phoneme_model`.

These are candidates only: they need a stage-specific run before removal. They
do not count as proof for any artifact type. The keys that were read but had
zero-effect perturbations remain UNPROVEN until a stronger value exercises the
relevant local code path.
