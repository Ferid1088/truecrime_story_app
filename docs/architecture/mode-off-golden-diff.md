# Full-documentary mode-off golden — 9D Part A acceptance

The pre-incremental boundary is `05320d570caf37a668d530814216ac9b3599d235`. It is the
parent of `13763f376143e946b76a1862395e3cb20bc0967e` (“Add opt-in hash-aware
stage skipping”). A detached worktree at `05320d5` was created and received
only the Phase-2 mock patch (`3ef4146`) plus the test-only payload recorder
needed for diagnosis. The worktree was removed after the comparison.

A1 used two independent fresh processes, databases, and output roots for each
tree. The current-tree test is
`tests/test_visual_production.py::test_four_language_independent_run_determinism`
(lines 904–945), and each child executes
`test_four_language_shadow_pipeline_real_job` once (the child guard is lines
804–812). The direct comparison output was:

```
PRE_REF_INDEPENDENT_RUNS 2 SNAPSHOT_DIFF 0
BASE /private/tmp/currentind2.gpRF0G DIFF 0
```

The current run has 181 snapshot keys. The only nondeterminism found was
concurrent language completion affecting music-history observation and ASR
append order. The fix is explicit `fa, ar, en, de` persistence order in
`app/documentary/jobs.py:545-551,558-571`, stable music-history ordering in
`app/documentary/music.py:307-320`, and snapshot canonicalization in
`app/services/soundness.py:19-38,49-82`. The host fake's mutable
call-count seed was also replaced by a complete request hash at
`tests/test_host.py:181-193`. The named volatile snapshot fields now include
generated primary/FK identifiers, timestamps, derived script hashes, and
`music_history_snapshot`; selected music identity and rendered bytes remain
hashed.

A2 recorded complete payload JSONL entries at
`tests/mock_payload.py:66-93`. The comparison matched provider/stage/language/
sequence after removing the old recorder's four duplicate nested HostGen records.
Both trees had 96 provider calls. Five ordinal ASR calls differed:
`asr/transcribe/de` sequences 1 and 3, and
`asr/transcribe/en` sequences 0, 2, and 3. The exact changed JSON paths were
under `.audio` and `.sidecar` (15 leaf paths for the block swaps, 373 for
the full block swaps). The ASR request multiset was identical; these were
parallel block completion-order artifacts, not different logical inputs.
Before the HostGen fix, four `generation/host_critic/<language>` calls also
differed at `.user.segments[0|1].dialogue`; the cause was
`tests/test_host.py:183` using `len(self.calls)`. The complete request hash
fix removed those four differences.

The incremental policy field appeared in generation payloads before the fix.
`tests/mock_payload.py:117-127` removes only
`production_policies.incremental` from provider-generation payloads because
it controls reuse, not generation. Absolute run roots are canonicalized by
`tests/mock_payload.py:12-56`. The only post-ref pipeline commit to inspect
was `13763f3`; its changes are StageRunner policy/reuse bookkeeping
(`app/documentary/pipeline/base.py:99-214`), not provider request construction.
No unexplained class-(c) provider-input behavior remained.

A4 final acceptance command compared the independent old-ref snapshot from
`05320d5+`Phase-2 mocks` with the current mode-off snapshot:

```
MODE_OFF_SNAPSHOT_DIFF 0
```

Both snapshots contain 181 keys. The golden file is
`tests/golden/documentary_pipeline_pre_incremental.json`. The comparison
assertion is `tests/test_visual_production.py:885-896`. No type was promoted
to SOUND by this part.

## Step 1 chapter decision — 9F

The authorized chapter change makes the chapter writer/auditor one-language
call per language. The neutral chapter-plan observation remains shared; the
localized card observation is per language. The current mode-off run used the
same test-only request-fingerprint mock mode as the accepted baseline
(`SOUNDNESS_CHAPTER_PAYLOAD=1`). The final diff had 13 keys, all justified:

```text
db:chapter_plans:0
db:production_scripts:0
db:production_scripts:1
db:production_scripts:2
db:production_scripts:3
file:cases/1/production/ar/production_v5_1.json
file:cases/1/production/de/production_v3_1.json
file:cases/1/production/en/production_v2_1.json
file:cases/1/production/fa/production_v4_1.json
file:cases/1/renders/ar/pilot_v5_1.mp4
file:cases/1/renders/de/pilot_v3_1.mp4
file:cases/1/renders/en/pilot_v2_1.mp4
file:cases/1/renders/fa/pilot_v4_1.mp4
```

`db:chapter_plans:0` is the changed persisted chapter result. The four
production scripts consume localized chapter cards, and the four production
JSON/renders consume those scripts. No upstream story, visual, voice, host,
audio, or library key changed in this controlled comparison. The updated
golden is `tests/golden/documentary_pipeline_pre_incremental.json`.
