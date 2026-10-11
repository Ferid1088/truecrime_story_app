# Operations runbook

## Daily glance
Operations page in the UI (failed calls, cost, job queue) and `python scripts/smoke.py <api-url> <token>`.

## Backups
- SQLite: `python scripts/db_tools.py backup truecrime.db backups/$(date +%F).db` — consistent copy while the app runs, verified after writing (table and row counts, integrity check).
- Postgres: `docker compose exec -T postgres pg_dump -U truecrime -Fc truecrime > backups/$(date +%F).dump`.
- Media: `data/cases/` (volume `tc_data` in Docker) is regenerable but slow to regenerate; include it in file backups.
- Keep `.env` in a password manager, never in the backup folder.
- Schedule: daily database backup, keep 14 daily + 6 monthly; copy off the machine.

## Restore drill (do it once now, then every quarter)
1. Restore the latest backup to a NEW database/file, never over the live one.
2. `python scripts/db_tools.py verify <restored.db>` (SQLite) or `pg_restore --list` and a row-count comparison (Postgres).
3. Start a second API instance pointed at it on another port and run `scripts/smoke.py` against it.
4. Write the date and result at the bottom of this file.

## Incidents
| Symptom | First look | Usual cause / action |
|---|---|---|
| UI shows "API offline" | `docker compose ps`, API logs | API crashed or migrating; `docker compose logs api` |
| Documentary job stuck "running" | Operations → jobs; worker logs | Worker died; it is resumed automatically after the lease (`JOB_LEASE_SECONDS`), or press Resume |
| Many failed calls to one host | Operations → Services / latest failures | Provider outage or expired key; rotate key (`docs/security.md`) |
| Login says "Too many failed attempts" | wait a minute | lockout is per address |
| Disk filling up | `du -sh data/cases` | old renders; delete cases' `renders/` folders |

## Log lines
JSON, one per line: `{"ts","level","logger","msg","request_id", ...}`. Find everything about one failing click by its request id (shown in the `X-Request-ID` response header and on the Operations page).

## Restore drill log
(none yet)
