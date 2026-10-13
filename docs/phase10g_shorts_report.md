# Prompt 10G2 — Shorts production evidence

## A. Scope and reuse

The production worker is `app/shortform/pipeline.py:302-377`. It accepts only
`completed`/`completed_warnings` long-film jobs, checks the case switch, selects
from finished language films, and isolates each candidate/language render. The
long-film job and its files are never mutated by a Shorts failure.

| Existing module | What was reused or replaced |
|---|---|
| `app/shortform/director.py:73-120` | Reused beat extraction, concept diversity, evidence/asset references, and chapter/time-range metadata. The legacy `generate_for_blueprint_row(..., persist=True)` compatibility path remains for old `ShortFormConcept` rows; the production worker persists `ShortCandidate` rows. |
| `app/shortform/selection.py` and `gates.py` | Reused MMR selection plus reveal-firewall, epistemic, rights, disclosure, duration, and automation gates. |
| `app/shortform/export.py:159-270` | Reused the package contract, cover, SRT, safe-area metadata, FFmpeg encoding, source-film audio, vertical crop, and RTL layout metadata. It now fails closed when required subtitle burning is unavailable. |
| `app/shortform/operations.py` | Kept as the publishing/export-handoff contract. Publishing remains later by design. |
| Old UI fixture identity | `Case 6 / Karmelo Anthony`, `C6-S01..S05`, and “Verification data only” are absent from the normal `/api/short-form/workspace` path. The remaining `SFC_demo_01` / Short 101 data is explicitly test-only at `frontend/e2e/fixtures.ts:375-389`; the test fixture now serves a real MP4 at `frontend/e2e/fixtures.ts:631-635`. |

There is no seed/fixture candidate in the normal production API path: when no
completed job exists, `app/shortform/api.py:89-100` returns an empty candidate
and render list. The legacy blueprint candidate endpoint is retained only for
compatibility and is not used by the production Short-Form page.

## B. Defaults and persistence

Config, not UI constants, at `config/parameters/documentary.json:150-163` and
validated by `app/core/ai_config.py:2020-2044`:

- 3–5 Shorts per finished film per language; chosen target: 4.
- 20–60 seconds; chosen target: 35 seconds.
- 1080×1920 portrait.
- Captions burned in for production, with a timed SRT retained as the canonical
  review artifact.
- Languages: English, German, Persian, Arabic (`en`, `de`, `fa`, `ar`).
- `fa` and `ar`: RTL direction and GeezaPro RTL font profile in
  `app/shortform/export.py:87-100`; libass is required for final video shaping.
- Maximum writer/auditor redos: 2.
- Default visibility: private.
- Case Shorts switch: enabled unless explicitly disabled.

Tables and graph:

- `ShortCandidate`, extended `ShortFormScript`, and `RenderedShort` are at
  `app/db/models.py:1113-1203`; migration is
  `migrations/versions/0011_shorts_production.py`.
- Artifact types/scopes/dependencies are at
  `app/core/artifact_dependencies.py:20-120`; soundness status is `UNPROVEN`
  with skip disabled in `config/soundness_status.json`.
- Deterministic files are under
  `data/cases/<case>/shorts/job_<job>/<language>/<candidate>/<hash>/`, with
  temp-directory creation and atomic rename in `app/shortform/pipeline.py:245-299`.

## C. E1–E6 evidence

| Point | Test file:line | What it asserts | Result |
|---|---|---|---|
| E1 finished film → automatic Shorts start | `tests/test_shorts_production.py:55-70`, automatic launcher `:145-160`; production hook `app/documentary/jobs.py:929-934` | A completed two-language job creates persisted candidates, per-language rendered rows, deterministic files, and completed Shorts stages; the finished-job hook queues the isolated worker. | Pass. The test producer uses a mock exporter for offline isolation. Real-source acceptance is blocked by the missing libass capability documented below. |
| E2 short failure isolation | `tests/test_shorts_production.py:73-100` | Injected German failure leaves English done, German failed, long-film job status unchanged, and the long-film bytes SHA-256 unchanged. | Pass. |
| E3 no premature start | `tests/test_shorts_production.py:102-106` | `running` and `failed` long-film jobs create no Shorts candidates. | Pass. |
| E4 one-language/one-short edit scope | `tests/test_shorts_production.py:163-195`; UI preview/apply `frontend/e2e/phase12-shortform.spec.ts:17-24` | Editing one German render changes only that render hash; other Shorts/languages remain byte/content-hash stable. UI actions require preview then apply. | Pass. |
| E5 real Playwright Short-Form interaction | `frontend/e2e/phase12-shortform.spec.ts:26-48` | Serves a real H.264/AAC MP4, waits for decoded 1080×1920 media, starts playback, observes time advance, and shows caption/cover metadata. | Pass in the 53-test suite. |
| E6 provider-call isolation/counters | `tests/test_stage_control_isolated.py:88-109`, network guard `tests/conftest.py:308-315` | Isolated stage actions produce exactly the expected adapter counter (`spoken: 1`, `voice: 1`) and no external network; Shorts writer/auditor/export tests use deterministic adapters or injected exporters. | Pass. |

## D. Real render proof and exact FFmpeg blocker

### Short 101 diagnosis

The prior UI fixture supplied a video URL but did not serve a media file, so the
browser had no decodable media and showed black/empty playback. The test fixture
now serves `frontend/e2e/assets/short-101.mp4`, generated with:

```text
ffmpeg -f lavfi -i testsrc2=size=1080x1920:rate=30 -f lavfi -i sine=frequency=440:sample_rate=48000 -t 3 -r 30 -c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart short-101.mp4
```

`ffprobe` output for that real media:

```text
video: codec=h264, width=1080, height=1920, nb_frames=90
audio: codec=aac, sample_rate=48000, nb_frames=142
format duration=3.000000
```

The full browser suite confirms decoded dimensions and playback time advances;
the old black-screen behavior was a missing/unsatisfied fixture URL, not an
empty MP4.

### Capability check

Observed local executable:

```text
ffmpeg version 9.0.1
configuration: ... --enable-libx264 ... --enable-libx265 ... --enable-videotoolbox ...
```

`ffmpeg -filters | grep -Ei 'subtitles|ass|libass'` returned no `subtitles` or
`ass` filter. The configuration contains no `--enable-libass`. `brew info
ffmpeg` identifies the installed standard formula; `brew info ffmpeg-full`
shows the alternate formula depends on `libass`, `harfbuzz`, and `freetype`.

The exact unexecuted Mac fix is:

```text
brew install ffmpeg-full
export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"
```

The app now probes this at startup and `/health` via
`app/shortform/export.py:29-77`, `app/main.py:27-42`, and
`app/api/system.py:29-36`. A real source-film render calls
`require_caption_capability()` at `app/shortform/export.py:204-211` and raises
`FFmpegCapabilityError` with the exact fix. The old silent uncaptioned fallback
was removed. Pipeline rows become `failed` with the reason, as proved by
`tests/test_shorts_production.py:109-142`.

### Diagnostic render and frames

Because this machine lacks the capability, no production-approved per-language
burned-caption render was claimed. To prove the video path and ffprobe output,
I ran the exporter explicitly with `captions_burned_in=False` into an ignored
diagnostic directory. Each package reports
`static_preview_plus_timed_srt`, not a production captioned render, and has the
same H.264/AAC, 1080×1920, 3.000s, 90-frame output.

Extracted diagnostic frames are available for inspection:

- [English frame](ui/shorts-frames/en.png)
- [German frame](ui/shorts-frames/de.png)
- [Persian frame](ui/shorts-frames/fa.png)
- [Arabic frame](ui/shorts-frames/ar.png)

These frames intentionally show the testsrc2 picture without claiming burned
captions. After installing `ffmpeg-full`, rerun the exact production command
through the worker; the four language renders will be gated on libass and
`fa`/`ar` will use the RTL subtitle layout.

## E. UI fixes and live proof

- `frontend/components/documentary/stage-control.tsx:463-479` now renders
  active/done/failed stages normally and collapses unavailable stages into one
  line per group, e.g. `32 stages not in this run — show`.
- `frontend/components/documentary/shared.tsx:193-219` and stage-control
  reason helpers map raw operational reasons to plain wording, including
  “The long film must finish before Shorts can start.”
- The Short-Form page remains persisted-data-only in the normal API path;
  only the Playwright fixture contains Short 101.

Automatic-start isolation proof is complete at the worker/unit level and the
post-film hook. The full isolated stack proves the real API, PostgreSQL, stage
scope, frontend, and cleanup contract, but a real source-film Shorts render on
this Mac stops at the required caption capability gate. It therefore produces
failed per-language Shorts rows rather than falsely claiming playable
production outputs.

## F. Full gate

- Final backend full suite: **1,139 passed, 9 skipped, 1 warning**.
- Frontend inventory: **53 tests**, not 49. Full run: **53 passed** in 1.1m.
  The prior 7-test isolated run was only a Shorts/stage subset; the full run
  used `E2E_PLAYWRIGHT_ARGS='' scripts/e2e_stack.sh` with disposable
  PostgreSQL and then dropped `truecrime_e2e`.
- Isolated PostgreSQL backend companion: **3 passed**.
- SQLite: `sqlite_cases=0`.
- Live PostgreSQL: `postgres_cases=0`, Alembic `0011`.
- Migration cycle: PostgreSQL `0009 → head (0010, 0011) → downgrade → head`
  passed; the full isolated stack also applied migrations `0001` through
  `0011` and dropped its database.
- Docs tests: included in the full backend suite; all passed.
- Quality ratchets: `ruff: 231 unique current, 0 new`; `mypy: 280 unique
  current, 0 new`.
- Secret scan: `OK secret scan: nothing found`.
- Frontend: ESLint, TypeScript `npx tsc --noEmit`, and `npm run build` passed.
- Four-language artifact soundness proof: `REGISTRY_MISMATCHES []`.
- No Docker and no OpenRouter calls were used.

Screenshots:

- [Pipeline desktop](ui/pipeline-shorts-desktop.png)
- [Pipeline phone](ui/pipeline-shorts-phone.png)
- [Short-Form desktop](ui/shorts-view-desktop.png)
- [Short-Form phone](ui/shorts-view-phone.png)

## G. Commits, restart, and honest not-done list

Previous implementation commit: `6140556ddf2a5ffaac15342cf53fdaed2d244792`.
The final report/code commit is verified with `git rev-parse HEAD` after the
final gate and services restart; the handoff records that final hash.

Not done / explicit blockers:

1. This Mac’s installed FFmpeg cannot burn captions. No production Shorts are
   accepted without libass; install `ffmpeg-full` using the command above and
   rerun the isolated real-film proof.
2. The four diagnostic frames are not approved production caption frames.
3. The writer/auditor seam is deterministic/offline; provider-backed Shorts
   generation is not enabled.
4. 10H per-language approval bundling and publishing adapters remain later
   work.
5. Automatic launch is an in-process worker task rather than a durable queue;
   restart recovery for incomplete Shorts jobs remains future work.
