# 9I Part 3 — golden provenance finding

Status: failed acceptance; Part 4 was not started.

The required provenance ref is `05320d570caf37a668d530814216ac9b3599d235`.
It was selected because it is the parent of `13763f376143e946b76a1862395e3cb20bc0967e`,
the first opt-in hash-aware stage-skipping commit.

## Procedure

`scripts/verify_golden_provenance.sh` creates a detached worktree at that ref,
applies the Phase-2 mock commit `3ef4146786c21de04d4d4d2688b731f2ab655ff3`,
checks out only the provider-boundary test helpers from `66cce6045850c8de797bd60b1849c2999ec3ed22`,
and applies the temporary snapshot-capture patch. The temporary worktree had no
`app/` changes:

```text
git -C /tmp/tc-9i-golden-clean-JvrmnH diff --stat 05320d5 -- app
(no output)
```

The Phase-2 patch had the following committed scope before the provider-boundary
overlay: `tests/mock_payload.py`, `tests/test_input_sensitive_mocks.py`,
`tests/test_spoken_audio.py`, `tests/test_visual_production.py`, and
`tests/test_voice_render.py`. The provider-boundary overlay changed only test
helpers and the temporary capture hook; it did not change application code.

## Evidence

The pre-ref pipeline passed independently in the temporary worktree:

```text
1 passed, 1 warning in 25.18s
REGISTRY_MISMATCHES []
```

Its snapshot contained 193 keys. Comparing it byte-for-byte with
`tests/golden/documentary_pipeline_pre_incremental.json` failed:

```text
PRE_REF_VS_COMMITTED_GOLDEN_DIFF 25
```

The first strict capture (the manually ported provider-boundary overlay and the
four snapshot roots used by the accepted 9H helper) had these 25 differing
keys:

```text
db:cases:0
db:chapter_plans:0
db:production_scripts:0
db:production_scripts:1
db:production_scripts:2
db:production_scripts:3
db:videos:0
db:videos:1
db:videos:2
db:videos:3
db:visual_assets:0
db:visual_assets:1
db:visual_assets:5
db:visual_assets:6
db:visual_audits:0
db:visual_audits:1
db:visual_audits:2
file:cases/1/production/ar/production_v5_1.json
file:cases/1/production/de/production_v3_1.json
file:cases/1/production/en/production_v2_1.json
file:cases/1/production/fa/production_v4_1.json
file:cases/1/renders/ar/pilot_v5_1.mp4
file:cases/1/renders/de/pilot_v3_1.mp4
file:cases/1/renders/en/pilot_v2_1.mp4
file:cases/1/renders/fa/pilot_v4_1.mp4
```

The authoritative grouped form is:

```text
db:cases:0
db:chapter_plans:0
db:production_scripts:0..3
db:videos:0..3
db:visual_assets:0,1,5,6
db:visual_audits:0..2
file:cases/1/production/{ar,de,en,fa}/production_v*_1.json
file:cases/1/renders/{ar,de,en,fa}/pilot_v*_1.mp4
```

The current-tree mode-off run passed and matched the committed golden. The
command was
`env -u TRUECRIME_DATABASE_URL -u DATABASE_URL PYTHONPATH=. .venv/bin/pytest -q -s tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job`;
the assertion is in `tests/test_visual_production.py:1592-1608`:

```text
1 passed, 1 warning in 63.73s
MODE_OFF_GOLDEN_DIFF {"different": 0, "keys": [], "pre_ref": "05320d5"}
GOLDEN_CMP_EXIT:0
CURRENT_MODE_OFF_VS_COMMITTED_GOLDEN_DIFF 0
```

The direct pre-ref versus current mode-off comparison was also 25 keys, the
same set above. The application commits responsible for the post-ref behavior
are observable in the history: the chapter-plan/card and downstream changes
are in `25cf3aa` (`app/agents/chapters.py` and
`app/documentary/jobs.py`), while the current golden was committed by `66cce60`
after those changes. Thus the committed golden is not provenance-valid for the
strict `05320d5 + mocks only` requirement. This is an unexplained golden
provenance failure for this part, not a volatile-field difference.

Per the prompt's acceptance rule, this finding blocks Part 4 and remains
UNPROVEN; the golden was not silently regenerated.

The committed verification script was then run as a second reproduction using
the exact helper files from `66cce60`. It also failed, returning
`PRE_REF_VS_COMMITTED_GOLDEN_DIFF 29`. The additional four keys were
`file:intros/{ar,de,en,fa}/intro_320x180.mp4`; the other 25 keys were identical
to the strict capture. This is a further preserved harness-boundary finding,
not a reason to discard the 25-key result. Both runs fail the required zero
diff acceptance.
