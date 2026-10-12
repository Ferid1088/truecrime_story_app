# Full-documentary mode-off golden — Phase 3b finding

The selected pre-incremental boundary is `05320d570caf37a668d530814216ac9b3599d235`.
It is the parent of `13763f376143e946b76a1862395e3cb20bc0967e`, whose subject is
“Add opt-in hash-aware stage skipping”. A detached worktree at `05320d5` was
created, and only Phase 2 commit `3ef4146` was cherry-picked into it. The
worktree diff was exactly these five mock/test files:

```
tests/mock_payload.py
tests/test_input_sensitive_mocks.py
tests/test_spoken_audio.py
tests/test_visual_production.py
tests/test_voice_render.py
```

The old-ref command was:

```
SOUNDNESS_CAPTURE=/private/tmp/golden-pass.json \
PYTHONPATH=/private/tmp:. .venv/bin/pytest -q -p capture_soundness_snapshot \
tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job
```

It passed: `1 passed, 1 warning in 23.22s`. The captured golden contains 181
snapshot keys. The temporary worktree was used only for this capture and was
removed after the comparison work.

The current mode-off command was the same test from the current checkout. Its
first-run snapshot was compared by `tests/test_visual_production.py:885-896`,
using the saved-output snapshot implementation at
`app/services/soundness.py:70-99`; the actual side does not read registry
output hashes (`app/services/soundness.py:70-76`). The result was not
identical: the latest comparison was `golden=181 current=181 diff=110`.

The changed-key counts were:

| snapshot scope | changed keys |
|---|---:|
| `chapter_plans` | 1 |
| `production_scripts` | 4 |
| `story_versions` | 4 |
| `visual_assets` | 4 |
| `visual_audits` | 3 |
| `voice_performances` | 4 |
| files | 78 |

The differences are not explained as expected Phase 2 changes. The raw
comparison showed request-sensitive markers differing in `story_versions`,
`visual_assets`, `visual_audits`, and `voice_performances`, with the resulting
audio/render files also differing. The snapshot now canonicalizes only the
explicit volatile column set at `app/services/soundness.py:18-24`, and
canonicalizes temporary output roots at `app/services/soundness.py:78-79`;
that removed path/ID noise but did not remove the 110 differences.

Per the 9D stop rule, this is an unexplained mode-off behavior difference from
the pre-incremental+new-mocks golden. Phase 3b is therefore incomplete and no
soundness result is promoted.
