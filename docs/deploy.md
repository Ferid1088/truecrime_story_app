# Deploying with Docker

One command starts the whole stack: Postgres, the API, the documentary worker, the web UI and the search engine.

```bash
cp .env.example .env
#   set TRUECRIME_ACCESS_TOKEN      (python -c "import secrets; print(secrets.token_urlsafe(32))")
#   set TRUECRIME_POSTGRES_PASSWORD (a long random string)
#   add your provider keys (APIMaster, ElevenLabs, ...)
docker compose up -d --build
python scripts/smoke.py http://127.0.0.1:8000 "$TRUECRIME_ACCESS_TOKEN"     # expect: SMOKE: GREEN
```

Open http://127.0.0.1:3000 and sign in with the access token.

## What runs
| Service | Role | Published |
|---|---|---|
| `postgres` | database (volume `tc_pg`) | no |
| `api` | FastAPI; applies database migrations at start | 127.0.0.1:8000 |
| `worker` | runs documentary jobs (`JOB_RUNNER=external`), restarts them after a crash | no |
| `web` | Next.js UI | 127.0.0.1:3000 |
| `searxng` | search engine | 127.0.0.1:8085 |

Media files live in volume `tc_data` (`/app/data`). Compose refuses to start without `TRUECRIME_ACCESS_TOKEN` and `TRUECRIME_POSTGRES_PASSWORD`.

## Hardening that is already on
Non-root users (uid 10001), all Linux capabilities dropped, `no-new-privileges`, multi-stage images without build tools, health checks on api/web/postgres, start order that waits for healthy dependencies, nothing published beyond loopback, JSON logs.

## Reaching it from other machines
Put a TLS reverse proxy (Caddy, nginx, Traefik) in front of ports 3000 and 8000, then in `.env`: `TRUECRIME_COOKIE_SECURE=true`, `TRUECRIME_CORS_ORIGINS=https://your-web-host`, `TRUECRIME_PUBLIC_API_URL=https://your-api-host`, and rebuild the web image (`docker compose up -d --build web`) because the API address is compiled into it. Web and API must share a registrable domain (e.g. `app.example.com` and `api.example.com`), since the session cookie is `SameSite=Strict`.

## Moving your existing SQLite data in
Follow `docs/postgres.md` (copy tool + checks), pointing `TRUECRIME_DATABASE_URL` at the compose Postgres, then copy `data/cases/` into the `tc_data` volume.

## Updating
`git pull && docker compose up -d --build`. The API applies new migrations on start; the worker waits for it. Back up first: `docker compose exec postgres pg_dump -U truecrime truecrime > backup.sql`.

## Limits worth knowing
- The container file system is writable for api/worker because the dynamic-EQ setting is saved into `config/`; that single change is lost when the image is rebuilt.
- CI builds the images with `TRUECRIME_PY_REQUIREMENTS=requirements.txt` (no torch/whisper) to stay fast; your own build includes them by default.

## Backups from the web UI
The Dashboard has a backup card (see `docs/operations.md`). In Docker, `TRUECRIME_BACKUP_DIR` (default `./backups`) is mounted as `/backups`; enter `/backups` as the folder. The directory must exist and be writable by uid 10001 (`mkdir -p backups && sudo chown 10001 backups`).
