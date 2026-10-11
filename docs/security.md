# Security

## Access control
- **`ACCESS_TOKEN` empty (default):** the API only answers requests that come from the same machine (loopback). Start uvicorn with `--host 127.0.0.1` so nothing else can even connect.
- **`ACCESS_TOKEN` set:** every endpoint except `/health` and `/api/auth/*` needs either `Authorization: Bearer <token>` or the session cookie that `/login` sets. Use this before exposing the app on a network.
  - Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
  - The cookie (`tc_session`) is signed, `HttpOnly`, `SameSite=Strict`, valid `SESSION_DAYS` (14). Changing `ACCESS_TOKEN` signs everyone out.
  - Behind HTTPS set `COOKIE_SECURE=true`.
- 5 wrong tokens within 60 s from one address lock that address out for a minute.
- Cookie-authenticated writes (POST/PUT/PATCH/DELETE) must come from an origin listed in `CORS_ORIGINS` (CSRF protection). Bearer-token clients are exempt.

## Responses
Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store` on API data.

## Files
Download endpoints serve a stored path only if it resolves (symlinks and `..` included) inside `data/`, the case storage folder, the thumbnail folder or the music library. A database row pointing elsewhere returns 404.

## Secrets
- Keys live in `.env` (never committed; the gate's secret scan fails the build if one is). `chmod 600 .env`.
- Rotate a key by changing it at the provider, updating `.env`, restarting the app.
- Back up `.env` separately from database backups.
