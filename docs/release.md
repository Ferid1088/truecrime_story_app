# Release and change process

## Every change
1. Work on a branch or directly on `main`; run `scripts/gate.sh` first (it is the same list CI runs).
2. Database change: add a migration, then rehearse it on a COPY of the real database (`docs/migrations.md`); the real file must stay byte-identical.
3. Push; wait for CI (backend, backend-postgres, frontend, docker) to be green. A red CI on `main` is fixed before anything else.

## Releasing
```bash
# 1. move "Unreleased" in CHANGELOG.md under a new version heading, commit
git tag -a v1.1.0 -m "v1.1.0" && git push origin v1.1.0
# 2. on the machine that runs the app:
python scripts/db_tools.py backup truecrime.db backups/pre-v1.1.0.db   # SQLite; for Postgres use pg_dump
git pull && docker compose up -d --build        # or restart uvicorn + worker
python scripts/smoke.py http://127.0.0.1:8000 "$ACCESS_TOKEN"            # expect GREEN
```
Rollback: migrations do not run backwards. Restore the backup taken in step 2, check out the previous tag, restart.

## Dependencies
- Dependabot opens weekly grouped pull requests for Python and npm, monthly for GitHub Actions and Docker bases.
- CI job `audit` fails on a known vulnerability in Python packages or in production npm packages, on every push and every Monday.
- Accepted risks are listed in `docs/security.md` with the reason and a review date.

## Making the process mandatory (optional)
Today only the owner pushes, so direct pushes to `main` are allowed and CI is advisory. When a second person joins: GitHub Settings → Branches → protect `main`: require a pull request, require the four CI jobs, require review from code owners (`.github/CODEOWNERS`).
