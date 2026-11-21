# Changelog

Format: newest first. Versions are git tags (`vMAJOR.MINOR.PATCH`); see `docs/release.md`.

## Unreleased
- Creative pack V4 (all cases, nine languages): exactly TWO creative requests per complete pack. GPT-6 reads the whole approved English master story, finds the strongest situations and writes for every language the film's title and cover text and each of five Shorts' narration, title and cover text with a plain-language factual anchor (one final version of each, free narration, no sections, no questions or calls to action required, no scores, backups, candidates or fact ids); GPT-Luna rewrites it all in ONE request from those texts and the anchors only (never the story or an example library); deterministic checks follow (structure, script, platform limits, no number, quotation or name the original and the anchor do not hold: an item that fails keeps GPT-6's wording; a failed Luna request keeps the GPT-6 result and says so). Tokens and cost are logged per model; a pack with unchanged input is never paid for twice (GPT-6 and Luna stages cached separately). Stored as proposals by `POST /api/cases/{id}/creative-pack` and once per case when the Autopilot reaches the titles (the case's selected languages); a compatibility adapter cuts a narration into the five parts the voice, avatar and video steps read; nothing approved, voiced or the owner's is replaced. Removed: the 630-title and 144-script libraries and the shared standards, the second-look and critic agents (title, native-language, hook), the old scout/writer/package/challenger flow, the alternative cover phrase, all spoiler checks of titles, covers, Shorts and publishing texts, the minimum word counts of titles and cover texts (now 1 to 18 words). Covers show the photograph of the victim or of the woman among victim and culprit, pinned behind the host (`covers.triptych.subject_photo`).
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
