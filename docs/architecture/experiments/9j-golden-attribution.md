# 9J Part 1 — golden provenance attribution

Continues `docs/architecture/experiments/9i-golden-provenance-failure.md`, which
left the 25-key (29 with the packaged verifier) diff UNPROVEN. This part
attributes every key to a commit and closes the finding.

## Method

`tests/golden/documentary_pipeline_pre_incremental.json` was touched by exactly
five commits between `05320d5` (exclusive) and `66cce60` (inclusive):
`573eeb2` (creates it), `de39976`, `d0422f4`, `25cf3aa`, `66cce60`. `66cce60`'s
own change is test-harness-only (9H Part 2 provider-payload fingerprinting,
already normalized by the packaged verifier's own boundary-checkout step), so
it cannot be the source of an unexplained diff. For each of the other three,
`git show <commit> -- tests/golden/....json | grep '^\+'` gives the exact set
of snapshot keys that commit's own diff touched:

```text
git log --oneline 05320d5..66cce60 -- tests/golden/documentary_pipeline_pre_incremental.json
66cce60 25cf3aa d0422f4 de39976 573eeb2
```

Intersecting those three key-sets against the 25 target keys, and taking the
most recent commit to touch each key as authoritative (chronological order
is `de39976` → `d0422f4` → `25cf3aa`):

| key group | commit | authoritative because |
|---|---|---|
| `db:chapter_plans:0`, `db:production_scripts:0..3`, `file:cases/1/production/*`, `file:cases/1/renders/*` (13 keys) | `25cf3aa` | newest of the three to touch these keys |
| `db:videos:0..3`, `db:visual_assets:0,1,5,6`, `db:visual_audits:0..2` (11 keys) | `d0422f4` | not touched by `25cf3aa`; `d0422f4` is the newer of the remaining two |
| `db:cases:0` (1 key) | `d0422f4` (primary), `de39976` (contributing) | not touched by `25cf3aa`; `de39976`'s own diff touches it but `d0422f4` is newer and, per the empirical runs below, independently sufficient to explain it |

## Classification

**`25cf3aa` — class (a), already accepted.** This is the 9F chapter-isolation
fix, already fully documented in `docs/architecture/mode-off-golden-diff.md`
("Step 1 chapter decision — 9F"), with the identical 13-key list. No new
investigation needed.

**`d0422f4` — class (a), already accepted (9D Part A), confirmed here with new
direct evidence.** The commit's own file list is
`app/documentary/jobs.py`, `app/documentary/music.py`,
`app/documentary/pipeline/language.py`, `app/services/soundness.py`, plus test
files — real application code, not test-only. `mode-off-golden-diff.md`
documents it as a determinism fix (explicit `fa, ar, en, de` persistence
order, stable music-history ordering, snapshot canonicalization), with A1/A2/A4
evidence that the fixed code is independently reproducible and that the
underlying provider-call content was unchanged by logical identity (same 96
calls; only parallel-completion ordinal position differed).

New direct confirmation for this part: the pre-`d0422f4` code (the pre-ref
worktree) was run twice independently (fresh temp DB, fresh temp output root,
same process-level mocks) and the two runs disagreed on exactly these 12 keys
(`db:cases:0` plus the 11 videos/visual_assets/visual_audits keys):

```text
RUN_A_VS_RUN_B_DIFF 12
db:cases:0 db:videos:0 db:videos:1 db:videos:2 db:videos:3
db:visual_assets:0 db:visual_assets:1 db:visual_assets:5 db:visual_assets:6
db:visual_audits:0 db:visual_audits:1 db:visual_audits:2
```

This reproduces, on demand, the exact raciness `d0422f4` was written to fix:
pre-`d0422f4` code is measurably nondeterministic on precisely this key set;
the current (post-`d0422f4`) code is not (`PRE_REF_VS_RUN_B_DIFF` against the
current committed golden is stable across repeated runs — see
`scripts/verify_golden_provenance.sh`'s own two-independent-runs proof below).
`db:cases:0`'s multiset values were also checked directly: the pre-/post-fix
hash sets are not a pure reindex (not simply the same 7 values in a different
order), consistent with real content differences driven by which language's
data a given slot receives, not merely a relabeling.

`db:cases:0` additionally has a contributing, independently-sufficient cause:
`de39976` changed `app/services/soundness.py` only (`SNAPSHOT_VOLATILE_JSON_KEYS`
filtering and `path_replacements` canonicalization of embedded JSON strings) —
a snapshot-computation change, not an application-code change. This alone would
change `db:cases:0`'s computed hash without changing the real `cases` row.
Both causes are recorded in `tests/golden/authorized_deltas.json`.

**No class (c) (unauthorized/unexplained) entries were found.** All 25 keys
trace to already-documented, already-accepted application-code changes.

## Part 1 step 3 — the 4 extra INTRO FILE differences (packaged verifier only)

The packaged verifier showed 29 differences (25 + `file:intros/{ar,de,en,fa}/intro_320x180.mp4`)
where a hand-ported reproduction showed only 25. Root cause, found by adding a
one-line diagnostic print ahead of the snapshot call in a disposable worktree:

```text
DIAG_INTRO_DIR 'data/intros' RESOLVED /Users/feri/truecrime_story_app/data/intros EXISTS True
```

`ChaptersConfig.intro_dir` (`app/core/ai_config.py`) defaults to the relative
path `"data/intros"`. `app/documentary/intros.py:intro_dir()` resolves a
relative value against the process's current working directory — not against
`SOUNDNESS_RUN_ROOT` or the worktree. The pre-ref worktree's `documentary_env`
fixture (built from `05320d5` + the phase-2 mock patch) predates the isolation
fix that the *current* `documentary_env` already has (it monkeypatches
`ai_config.chapters.intro_dir` to a per-test `tmp_path` subdirectory). Without
that line, `ensure_intro()`'s content-addressed cache resolves to the real,
shared, persistent `data/intros/` directory at the repo root — a cache keyed
only by the fingerprint of (version, concept, logo, size, fps, sample-rate),
reused across every process regardless of directory. This is a working-directory
/ test-isolation gap, not a rendering nondeterminism bug: cause (f) from the
prompt's own candidate list ("working directory"), not fonts/ffmpeg/paths/
timestamps.

Fix: `docs/architecture/experiments/9j-intro-dir-isolation.patch` ports the
same one monkeypatch line (`ai_config.chapters.intro_dir` → `tmp_path /
"intros"`) into the pre-ref worktree's `documentary_env` fixture, matching the
already-present pattern for `voice.work_dir`, `music_library.dir`, and
`documentary.storage_dir` immediately above it.
`scripts/verify_golden_provenance.sh` now applies this patch right after the
existing provider-boundary patch. This is test-harness-only; no `app/` file
changed.

Proof (two independent runs of the packaged verifier, each its own fresh
`mktemp -d` worktree and run root):

```text
run 1: exit=0, PRE_REF_VS_COMMITTED_GOLDEN_DIFF 25, GOLDEN_PROVENANCE_OK
run 2: exit=0, PRE_REF_VS_COMMITTED_GOLDEN_DIFF 25, GOLDEN_PROVENANCE_OK
diff between the two runs' key/commit listings: 0
```

No intro key appears in either run's diff any more; the count returned to the
already-attributed 25.

## Acceptance

`scripts/verify_golden_provenance.sh` now loads
`tests/golden/authorized_deltas.json` and passes iff every key where the
pre-ref-plus-mocks snapshot disagrees with the committed golden is listed
there with a commit and reason, and every listed key actually did differ this
run (a listed-but-unchanged entry also fails, so the file cannot silently
accumulate stale claims). Current state: `GOLDEN_PROVENANCE_OK`, 25 keys, 0
unauthorized, 0 stale.

Part 4 (blocked by the prior UNPROVEN finding) can now proceed in a later
session; it was not started here per the prompt's phased stop condition.
