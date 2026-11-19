# Changelog

Format: newest first. Versions are git tags (`vMAJOR.MINOR.PATCH`); see `docs/release.md`.

## Unreleased
- Creative pack (all cases, all nine languages): ONE request to GPT-6 (it sees every act and every verified fact of the case; nothing is held back) returns the film title and cover text, and for every Short its hook, script, title and cover text, for every language independently; the model scouts at least eight concepts with fact ids and five scores (the code picks the strongest, never one without a verified fact id), checks its answer twice inside the call, and the code validates the whole pack (7 to 18 words for every title and cover text, per-language word limits, empty suspense phrases in nine languages, real fact ids) with one repair request. The pack is stored as proposals (Short clips, title candidates, open cover texts; nothing approved, voiced or the owner's is replaced) by `POST /api/cases/{id}/creative-pack` and once per case when the Autopilot reaches the titles. All spoiler checks of titles, covers, Shorts and publishing texts are removed; the title, native-language and hook critics and the old scout/writer/package/challenger flow are removed (the title agent scores its own candidates); durations are labelled estimate or measured. The creative agents run on GPT-6. Covers show the photograph of the victim or of the woman among victim and culprit, pinned behind the host (`covers.triptych.subject_photo`).
- Parallel production: titles, Shorts and the dry-run publish queue no longer wait for every film (each language when its own inputs exist); Autopilot tick 20 s -> 3 s; the fixed 20 s gap between film starts is replaced by holding back the expected memory of films that just started (`orchestrator.settle_seconds`). `voice_stage` 9, `jobs` 9, `asr` 1 -> 4 (with `asr_check.workers` 4, a lock around the model load), `mix` 1 -> 9 (measured: `scripts/bench_asr.py`, `scripts/bench_mix.py`), new `image` gate (2) with Retry-After and possibly-billed marking for image requests, ElevenLabs requests that got no answer are recorded as `uncertain_prior_attempts`. Fixes: the opening stage read a removed `music.prompt` key; `approved_items` had no total order (PostgreSQL returned ties in another order); three review tests used a production script that did not exist (PostgreSQL enforces the key); two stale browser specs follow the Opening tab.
- Autopilot: one owner approval of the case's shared pictures and videos (`/api/cases/{id}/mvp/visual-approval`, button on Review pictures) is required before any language's film is directed or rendered; a changed package voids it, automated validation never counts as it, and stopped films go on by themselves after it. A language's film (and its voice) now starts as soon as its own final text is approved: the wait for the English film's visual searches is removed (other languages make no searches). `concurrency.voice_stage` 1 -> 3 (several languages are voiced side by side; the ElevenLabs, ASR and mix limits still apply). `render` stays 1: measured on the Mac Studio, one libx264 encode uses all cores and 1-6 parallel encodes give 5.0-5.5 video-seconds per second.
- Dashboard: database backup to a chosen folder (external drive) with "Back up now" and an automatic schedule (every N hours from a start time, timestamped files, keep last N). Migration 0006.

## 1.0.0 — enterprise hardening (October 2026)
### Data
- Schema versioned with Alembic (migrations 0001–0005); existing databases are recognised and adopted without data changes.
- PostgreSQL supported alongside SQLite, with a verified copy tool and runbook (`docs/postgres.md`).
- Database backup, verify and compare tools (`scripts/db_tools.py`).
### Reliability
- Documentary jobs can run in a separate worker with leases, heartbeats, automatic resume and crash recovery (`docs/jobs.md`).
### Security
- Access-token login with signed HttpOnly session cookie, CSRF origin check, lockout after repeated failures, hardening headers.
- File downloads confined to the project's data folders.
### Observability
- Structured JSON logs with request ids; ledger of every outgoing call (host, status, latency, tokens, cost); Operations page.
### Packaging
- Native macOS process runner for Postgres, API, worker, web, and search; smoke-tested in CI.
### Quality
- Test gate: Python suites on SQLite and Postgres, secret scan, type/lint/build, 46 browser tests including accessibility; API contract test between UI and backend.
- Dependencies upgraded to clear all known vulnerabilities (FastAPI/Starlette, pypdf, python-dotenv).
