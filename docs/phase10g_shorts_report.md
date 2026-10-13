# Prompt 10G3 — production Shorts proof

## A. Evidence and reuse

The production path is persisted and per-language: `app/shortform/pipeline.py:302-380`
accepts only a finished long-film job, selects from finished language films,
persists candidates, writes each language script/caption, and isolates each
render error. The completion hook is fire-and-forget at
`app/documentary/jobs.py:929-936`; a Shorts error cannot change the long-film
job.

| Area | Evidence | Result |
|---|---|---|
| Reused director/selection/gates | `app/shortform/director.py:45-120`, `app/shortform/selection.py`, `app/shortform/gates.py` | Beat/chapter extraction, MMR selection, rights, epistemic, reveal-firewall, and duration gates are reused. |
| Reused exporter | `app/shortform/export.py:196-282` | Cover, SRT, safe-area metadata, vertical crop, language audio, FFmpeg encode, content manifest. Production source renders now require caption burn-in. |
| Existing operations | `app/shortform/operations.py` | Reused for preview/apply handoff; publishing remains later. |
| Fixture replacement | `frontend/e2e/fixtures.ts:376-394,633-636` | `Case 6 / Karmelo Anthony`, `C6-S01..S05`, and “Verification data only” are not in the normal API/page path. The remaining `SFC_demo_01`/Short 101 identity is test-only and serves a real MP4. |

There is no fixture candidate in the normal UI path: an empty production
workspace is returned by `app/shortform/api.py:89-100`. The Short-Form page only
renders the persisted API response.

## B. Defaults and persistence

The chosen defaults are config values, not UI constants:

- 3–5 Shorts per finished film per language; target 4
  (`config/parameters/documentary.json:151-155`, validated by
  `app/core/ai_config.py:2020-2045`).
- 20–60 seconds; target 35 (`...documentary.json:155-157`).
- 1080×1920 portrait (`...documentary.json:158-159`).
- Burned-in captions plus timed SRT review artifact
  (`...documentary.json:160`).
- Languages `en`, `de`, `fa`, `ar`; default visibility `private`; maximum two
  writer/auditor redos (`...documentary.json:161-163`).
- Persian/Arabic are RTL in `app/shortform/export.py:106-118`; the checked
  caption font is Geeza Pro from `/System/Library/Fonts/GeezaPro.ttc`, resolved
  and existence-checked at `app/shortform/export.py:130-148`. The FFmpeg
  `force_style` explicitly selects the Geeza Pro family for `fa`/`ar` at
  `app/shortform/export.py:266-267`.

Persistence is in migration `migrations/versions/0011_shorts_production.py`
and models `app/db/models.py:1125-1214`: candidate film/version/source time
range/gates/approval, language script/caption data, render files/content hashes,
approval state, and failure state. Artifact types and dependencies are
`app/core/artifact_dependencies.py:126-128`; all three Shorts types remain
`UNPROVEN` with skip off in `config/soundness_status.json:73-75,167-170`.
The worker uses deterministic per-run paths and hash reuse in
`app/shortform/pipeline.py:245-299`.

## C. FFmpeg binary and capability proof

The configured binary is keg-only and is not linked into PATH:

```text
FFMPEG_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
FFPROBE_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffprobe
```

Typed config is at `config/parameters/documentary.json:164-165`; environment
overrides are resolved at `app/core/media_tools.py:15-29`. API and worker
inherit the absolute paths from `scripts/dev_up.sh:53-65`. The runbook is
`docs/ops/postgresql-runbook.md:24-43`. No PATH change and no `brew link` were
used.

The exact installed-build checks were:

```text
ffmpeg version 9.0.2 Copyright (c) 2000-2026 the FFmpeg developers
configuration: --prefix=/opt/homebrew/Cellar/ffmpeg-full/9.0.2 ... --enable-libharfbuzz ... --enable-libass ... --enable-libfontconfig --enable-libfreetype ...
 .. ass               V->V       Render ASS subtitles onto input video using the libass library.
 .. subtitles         V->V       Render text subtitles onto input video using the libass library.
```

`otool -L /opt/homebrew/opt/libass/lib/libass.dylib` showed both
`/opt/homebrew/opt/fribidi/lib/libfribidi.0.dylib` and
`/opt/homebrew/opt/harfbuzz/lib/libharfbuzz.0.dylib`. The capability result
used by startup and `/health` (`app/main.py:27-38`, `app/api/system.py:27-33`)
was:

```json
{"available":true,"executable":"/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg","ffprobe":"/opt/homebrew/opt/ffmpeg-full/bin/ffprobe","subtitles_filter":true,"ass_filter":true,"libass":true,"harfbuzz":true,"fribidi":true,"reason":null,"fix":null}
```

If the check fails, `require_caption_capability()` at
`app/shortform/export.py:92-97` raises `FFmpegCapabilityError`; the worker
records FAILED and never emits an uncaptioned production short. The exact Mac
fix is `brew install ffmpeg-full`, followed by the two absolute environment
variables above. It does not modify PATH.

## D. Real render proof

The stand-in long film was generated with the installed binary, no network:

```sh
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -y -v error \
  -f lavfi -i 'testsrc2=size=1920x1080:rate=30' \
  -f lavfi -i 'sine=frequency=440:sample_rate=48000' -t 20 \
  -map 0:v -map 1:a -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
  data/dev/shorts-proof/real/long-film-standin.mp4
```

The real `build_export_package()` was run for `en`, `de`, `fa`, and `ar` with
the pipeline caption text, `source_video=long-film-standin.mp4`, 20 seconds,
and `captions_burned_in=True`. This was not the frontend fixture asset.

`ffprobe` output for every production result under
`data/dev/shorts-proof/real/<language>/short.mp4`:

```text
en: codec_name=h264 width=1080 height=1920 nb_frames=600 duration=20.000000; audio codec_name=aac codec_type=audio
de: codec_name=h264 width=1080 height=1920 nb_frames=600 duration=20.000000; audio codec_name=aac codec_type=audio
fa: codec_name=h264 width=1080 height=1920 nb_frames=600 duration=20.000000; audio codec_name=aac codec_type=audio
ar: codec_name=h264 width=1080 height=1920 nb_frames=600 duration=20.000000; audio codec_name=aac codec_type=audio
```

The previous black Short 101 state was a test-route/media-serving problem, not
an empty production output. The frontend fixture now serves a real 1080×1920
H.264/AAC file and the Playwright test starts playback and observes time
advance (`frontend/e2e/phase12-shortform.spec.ts:26-56`).

Two frames per language were extracted with `ffmpeg -ss` from the real
captioned outputs. The old non-production `en.png`, `de.png`, `fa.png`, and
`ar.png` frames were deleted. New committed frames:

- [en-05.png](ui/shorts-frames/en-05.png), [en-15.png](ui/shorts-frames/en-15.png)
- [de-05.png](ui/shorts-frames/de-05.png), [de-15.png](ui/shorts-frames/de-15.png)
- [fa-05.png](ui/shorts-frames/fa-05.png), [fa-15.png](ui/shorts-frames/fa-15.png)
- [ar-05.png](ui/shorts-frames/ar-05.png), [ar-15.png](ui/shorts-frames/ar-15.png)

Visual inspection confirms picture and burned captions; the `fa`/`ar` frames
show joined glyphs, marks, and RTL layout.

## E. E1–E6 test evidence

| Point | Test file:line | Assertion | Result |
|---|---|---|---|
| E1 | `tests/test_shorts_production.py:56-71`; launcher `:165-180`; completion hook `app/documentary/jobs.py:929-936` | A finished two-language job creates persisted per-language rows/files/stages; the completion hook queues the isolated worker. | Pass. The test uses a mock exporter as required for the isolated stack. Real four-language production export also ran in the full soundness gate. |
| E2 | `tests/test_shorts_production.py:74-100` | Injected German failure leaves English done, German failed, long-film status unchanged, and long-film bytes SHA-256 unchanged. | Pass. |
| E3 | `tests/test_shorts_production.py:103-107` | `running` and `failed` long-film jobs create no Shorts candidates. | Pass. |
| E4 | `tests/test_shorts_production.py:183-221` | Editing only German caption changes only German render hash; other language hashes stay byte/content-hash stable. | Pass. |
| E5 | `frontend/e2e/phase12-shortform.spec.ts:26-56` | Real Playwright media decode, 1080×1920 dimensions, playback time advance, English then German render. | Pass in 53-test gate. |
| E6 | `tests/test_stage_control_isolated.py:88-109`, `tests/conftest.py:308-315` | Adapter counters are exact and network is blocked; no external provider calls. | Pass; isolated counters remain `spoken: 1`, `voice: 1`. |

The explicit fail-closed/capability tests are
`tests/test_shorts_production.py:110-139` and cover both `/health` reporting
and `FFMPEG_BIN`/`FFPROBE_BIN` environment precedence.

## F. UI proof

Inactive stage cards are collapsed per group at
`frontend/components/documentary/stage-control.tsx:463-477` into lines such as
`15 stages not in this run — show`; active, done, failed, and skipped-with-
reason cards remain visible. Plain-language reasons and the capitalization fix
are at `frontend/components/documentary/shared.tsx:99,193-200`.

New isolated-stack screenshots:

- [Pipeline desktop](ui/pipeline-shorts-desktop.png)
- [Pipeline phone](ui/pipeline-shorts-phone.png)
- [Short-Form desktop](ui/shorts-view-desktop.png)
- [Short-Form phone](ui/shorts-view-phone.png)

## G. Full gate and final process proof

- Backend: **1,141 passed, 9 skipped, 1 warning**. The long duration is
  expected: with the installed capability, the four-language soundness run
  also exercised real Shorts encodes instead of fail-closed skips.
- SQLite cases: **0**.
- Live PostgreSQL cases: **0**.
- Alembic: **0011 (head)**.
- Docs tests: included in the backend gate; pass.
- Ratchets: `ruff: 231 unique current, 0 new`; `mypy: 280 unique current, 0 new`.
- Secret scan: `OK secret scan: nothing found`.
- Frontend: lint, `npx tsc --noEmit`, and `npm run build` pass.
- Playwright: **53 passed** (the suite is 53, not 49); disposable companion
  E2E: **3 passed**. `truecrime_e2e` was dropped afterward.
- No Docker and no OpenRouter were used.

Implementation commit verified by `git rev-parse HEAD` before this report:
`f361a23dbd0dbd318dd2c8a94680228b06b76f2b`. Previous Shorts implementation
commit: `c1254625bcff20a0945761af259613b71ef2ac82`. The report commit and final
restart hash are recorded below after the report is committed.

## H. Not done / honest boundary

1. 10H approval bundling and publishing adapters are later work; Shorts remain
   private by default.
2. Provider-backed writer/auditor calls are not enabled in this no-network
   proof; deterministic mock producers were used for the isolated tests.
3. Automatic launch is an in-process worker task, not a durable external queue;
   restart recovery for an interrupted Shorts worker remains future work.
4. The legacy long-film renderers intentionally retain their established PATH
   FFmpeg contract because changing their codec binary changes their existing
   byte-level golden outputs. The configurable absolute binary requirement is
   applied to every production Shorts exporter and its capability/health path.
