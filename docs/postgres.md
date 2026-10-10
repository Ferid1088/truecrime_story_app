# Moving from SQLite to Postgres

The app runs on either. It uses SQLite until `DATABASE_URL` points somewhere else, so nothing
changes until you decide to switch. Switching is reversible: the SQLite file is never modified.

## One-time cutover (about 10 minutes)

1. Install Postgres 16 or newer (Homebrew: `brew install postgresql@16 && brew services start postgresql@16`;
   or any Docker image `postgres:16`).
2. Create an empty database:
   `createdb truecrime` (or `docker exec <container> createdb -U postgres truecrime`).
3. Stop the app and any running jobs. Take a backup and note its baseline:
   `python scripts/db_tools.py backup truecrime.db backups/`
   `python scripts/db_tools.py baseline truecrime.db backups/baseline-before-postgres.json`
4. Copy the data:
   `python scripts/sqlite_to_postgres.py truecrime.db postgresql+psycopg://localhost/truecrime`
   It checks first (values Postgres would refuse are listed and nothing is written), copies everything
   in one transaction, and finally compares every table row by row. It must end with
   `OK copy verified: 55 tables, ... rows identical on both sides`.
5. In `.env` set `DATABASE_URL=postgresql+psycopg://localhost/truecrime` and start the app.
6. Keep `truecrime.db` untouched as the fallback until the Postgres version has run your normal work
   for a few weeks.

## Go back

Remove the `DATABASE_URL` line (or point it at `sqlite:///./truecrime.db`) and restart. Anything made
on Postgres after the cutover is not in the SQLite file.

## Tests

`TC_TEST_DATABASE_URL=postgresql+psycopg://user@localhost/test_db python -m pytest` runs the whole suite on
Postgres (the database is wiped first, use a throw-away one). CI does this on every push.
