# Prompt 9H Part 5: preview consistency

The preview endpoint and the snapshot harness now share
`app.services.soundness.predicted_change_keys` (defined at
`app/services/soundness.py:349` and called by
`app/services/artifacts.py:157` and `tests/soundness_replay.py:55,344,363`).
Artifact edits use the persisted registry closure; typed configuration edits
use the declared `config_inputs` predictor. The API accepts
`changed_config_sections` so a configuration preview does not need a fake
artifact edit.

The full four-language registry test is
`tests/test_scope_preview.py::test_full_four_language_preview_contains_snapshot_diff_for_eight_perturbations`.
It creates all registered artifact types for `en`, `de`, `fa`, and `ar`,
connects them with the production dependency table, compares the preview set
with the committed snapshot-diff records, and verifies every table row count
is unchanged.

| perturbation | preview count | actual snapshot count | missing |
|---|---:|---:|---|
| logo bytes | 8 | 2 | 0 |
| naming | 13 | 11 | 0 |
| spoken version (de) | 10 | 9 | 0 |
| one beat | 48 | 6 | 0 |
| one voice take (de) | 6 | 1 | 0 |
| host plan | 13 | 1 | 0 |
| typed config: audio | 29 | 0 | 0 |
| used visual metadata | 12 | 12 | 0 |

The command produced `1 passed` and no database row-count change.
