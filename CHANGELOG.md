# Changelog

Format: newest first. Versions are git tags (`vMAJOR.MINOR.PATCH`); see `docs/release.md`.

## Unreleased
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
