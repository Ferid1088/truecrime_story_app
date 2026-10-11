# Operations runbook

## Daily glance
Operations page in the UI (failed calls, cost, job queue) and `python scripts/smoke.py <api-url> <token>`.

## Backups
**In the app (Dashboard → Database backup):**
- Type the folder (e.g. `/Volumes/MyDrive/truecrime-backups`) and press **Back up now**. The folder is remembered.
- **Automatic backups:** tick the box, set "every N hours", "starting at HH:MM" and "keep last N" (0 keeps everything), press **Save schedule**. Backups happen at the start time and then every N hours (every 24 h = once a day at that time; every 6 h from 02:00 = 02, 08, 14, 20). The app must be running at that moment; if the Mac was off or asleep, one backup is made as soon as it is back. A failed attempt (drive not connected) is retried after 30 minutes and shown in red in the list.
- Files are named `truecrime-backup-YYYYMMDD-HHMMSS.db` (Postgres: `.dump`, needs `pg_dump`). Old ones beyond "keep last" are deleted, but only files with exactly that name pattern.
- The folder is never created for you: if the drive is unplugged you get "Is the drive connected?" instead of a stray folder on the internal disk.
- Each backup is a consistent copy made while the app runs, checked (integrity, tables present), copied to the drive and compared by checksum before it gets its final name. Permissions are owner-only.
- Docker: set `TRUECRIME_BACKUP_DIR` in `.env` (host folder or external drive) and use `/backups` as the folder in the app.

**What a backup contains:** the database only (cases, sources, texts, settings, history). Generated media (`data/cases/`, volume `tc_data`) is not included; copy that folder separately (Finder, rsync) from time to time.

**By hand:**
- SQLite: `python scripts/db_tools.py backup truecrime.db <folder>`.
- Postgres: `docker compose exec -T postgres pg_dump -U truecrime -Fc truecrime > backups/$(date +%F).dump`.
- Keep `.env` in a password manager, never in the backup folder.
- Suggested: daily, keep 14; copy one backup off the machine now and then.

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
