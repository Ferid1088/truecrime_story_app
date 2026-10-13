# Prompt 10G — Shorts production report

## Outcome

Production Shorts now start only after a long-film job reaches `completed` or
`completed_warnings`. They are isolated by film job, language, and candidate;
Shorts errors are recorded on Shorts and never change the long-film status or
artifacts. The live case workspace is empty (`0` cases); Playwright data is
test-only.

## Evidence and reuse

| Existing area | Reused / disposition |
|---|---|
| `app/shortform/director.py` | Reused deterministic beat extraction, concept diversity, evidence/asset references; extended it with chapter/time ranges. |
| `app/shortform/selection.py`, `gates.py` | Reused MMR selection and reveal-firewall, epistemic, rights, disclosure, duration, and automation gates. |
| `app/shortform/export.py` | Reused FFmpeg package contract, cover, SRT, safe-area metadata, and RTL layout metadata; extended it to source-film video/audio and vertical crop. |
| `app/shortform/operations.py` | Remains the publishing/contract layer; publishing is intentionally later. |
| `app/shortform/director.py:73-120` | The old `generate_for_blueprint_row(..., persist=True)` path is compatibility/pilot-only for legacy `ShortFormConcept` rows. The worker uses persisted production rows instead. |
| old frontend short-form page/tests | Removed from product UI. `Case 6 / Karmelo Anthony`, `C6-S01..S05`, and “Verification data only” remain only where legacy tests/fixtures explicitly need them; production workspace has no seeded candidates. |

## Persistence and graph

- `short_candidates`, `short_form_scripts` extensions, and `rendered_shorts` are
  defined at `app/db/models.py:1113-1203` and migrated by
  `migrations/versions/0011_shorts_production.py`.
- Candidate rows store beat/chapter IDs, source time range, hook/CTA, claims,
  gate results, selection state, content hash, and approval state.
- Render rows store language, film version, caption text, all artifact paths,
  manifest, content hash, retry/error state, and approval state.
- Artifact types/scopes/dependencies are in
  `app/core/artifact_dependencies.py:20-120`; all three are registered as
  `UNPROVEN` in `config/soundness_status.json`, with skip disabled.
- The deterministic path is `data/cases/<case>/shorts/job_<job>/<lang>/<candidate>/<hash>/`.
  Rendering uses a temp directory and atomic rename; a matching content hash
  reuses the existing render.

## Pipeline and controls

- Worker and failure boundaries: `app/shortform/pipeline.py:302-374`.
- Film-finished guard and case switch: `app/shortform/pipeline.py:60-77,302-315`.
- Script/caption writer plus independent offline audit payload and two-redo
  limit: `app/shortform/pipeline.py:188-240,346-358`.
- Per-language render, cover, captions, content hashes, and clean-up:
  `app/shortform/pipeline.py:245-300` and `app/shortform/export.py:89-220`.
- Automatic post-film launch: `app/documentary/jobs.py:891-932`.
- Real Pipeline stages are added by `app/documentary/jobs.py:218-236`; per-short
  stage names are materialized by the worker, so one-short redo scope is
  addressable.
- Stage-control scope binding/short IDs: `app/services/stage_control.py:61-100,120-175`.
- Candidate/render preview+apply APIs and file serving:
  `app/shortform/api.py:110-280`.
- The UI is `frontend/app/short-form/page.tsx`; review is
  `frontend/app/short-form/review/page.tsx`. Represented/not-in-run stage
  cards are collapsed in `frontend/components/documentary/stage-control.tsx`;
  the stage label fix is `frontend/components/documentary/shared.tsx`.

## Chosen config defaults

These are config values, not UI constants (`config/parameters/documentary.json`
and `app/core/ai_config.py`):

- 3–5 Shorts per film/language; target 4.
- 20–60 seconds; target 35 seconds.
- 1080×1920 vertical.
- Burned-in captions enabled, with canonical timed SRT retained.
- Languages: `en`, `de`, `fa`, `ar`.
- Maximum script/audit redos: 2.
- Default visibility: `private`.
- Case-level Shorts switch defaults to enabled; publishing remains separate.

## Verification

- Full backend/repository gate: 1,135 passed, 9 skipped, 1 warning
  (`PYTHONPATH=. .venv/bin/pytest -q`, 18:04).
- The focused Shorts worker suite adds 3 passed tests in
  `tests/test_shorts_production.py`; the focused registry/worker checks were
  21 passed after the final hash-consistency fix.
- Isolated PostgreSQL browser stack: 7 Playwright tests passed, then the
  isolated PostgreSQL stage-control test passed (3 tests).
- Frontend: `npm run lint` and `npx tsc --noEmit` passed.
- Migration cycle: PostgreSQL 0009 → head → downgrade → head passed.
- Final four-language shadow proof reports `REGISTRY_MISMATCHES []`.
- Release checks: `git diff --check`, frontend ESLint, and TypeScript
  type-check pass. Ruff passes on the newly changed model/artifact files;
  legacy short-form modules retain pre-existing style findings outside this
  change.
- Final commit and process/DB evidence are appended below after restart.
- Screenshots:
  - [Pipeline with Shorts stages](ui/pipeline-shorts-desktop.png)
  - [Pipeline with Shorts stages — phone](ui/pipeline-shorts-phone.png)
  - [Short-Form real render — desktop](ui/shorts-view-desktop.png)
  - [Short-Form real render — phone](ui/shorts-view-phone.png)

## Not done / limits

1. The writer/auditor seam is deterministic and offline in this change; a real
   provider-backed Shorts writer/auditor is not enabled. No OpenRouter or
   external calls are used.
2. This checkout’s FFmpeg has no `subtitles`/libass filter. The exporter
   safely falls back to a vertical render plus timed SRT and records that mode
   in the manifest; production burned-in RTL output requires an FFmpeg build
   with libass/subtitles enabled.
3. The 10H per-language approval bundle and publishing adapters are not wired
   yet; approval fields and per-language render rows are ready for that later
   bundle.
4. Automatic launch is an in-process worker task in the existing worker
   architecture, not a new durable queue. A future restart-recovery pass should
   resume incomplete Shorts jobs from persisted statuses.

## Release evidence

- Implementation commit verified with `git rev-parse HEAD`: 
  `6140556ddf2a5ffaac15342cf53fdaed2d244792`.
- At verification time, API/worker/frontend were running from the repository
  checkout at `/Users/feri/truecrime_story_app`; `ps` and `lsof` showed the
  expected commands and checkout CWDs, and the tree was clean.
- API health returned `{"status":"ok"}`; the live Short-Form route returned
  HTTP 200. Final release-evidence commit and its matching restart are recorded
  in the handoff after this report update.
- Live DB state: `cases=0`, Alembic head `0011` before final restart.
