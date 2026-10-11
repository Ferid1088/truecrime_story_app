# Observability

## Logs
- `LOG_FORMAT=text` (default) or `json` (one object per line: `ts, level, logger, msg, request_id`, plus `method/path/status/ms` on access lines). `LOG_LEVEL=INFO`.
- Every API request gets a request id (`X-Request-ID` is echoed in the response; a client-supplied id is accepted only if it is 1-64 letters, digits, `.`, `_`, `-`). Log lines and ledger rows written while handling that request carry it, so one failing click can be followed end to end.
- Access lines contain the path, never the query string. `/health` is not logged.
- The `httpx` library's own INFO lines (which contain full URLs) are turned down to warnings.

## Provider-call ledger
Every outgoing HTTP call (LLM, voice, search, stock footage, maps...) is recorded in table `provider_calls`: host, method, path without query, status, duration, bytes, and for LLM-style responses the token counts and cost (`usage.cost` when the provider reports it). Keys, headers, request and response bodies are never stored. Rows older than `PROVIDER_LEDGER_DAYS` (90) are deleted at start. Switch off with `PROVIDER_LEDGER=false`.

Writes go through a queue to one background thread, so a slow or broken database never slows or fails a call.

## Endpoints (need sign-in like the rest of the API)
- `GET /api/ops/summary?hours=24` — per host: calls, errors, p50/p95 ms, tokens, cost; plus documentary jobs by status.
- `GET /api/ops/provider-calls?limit=100&only_errors=true&host=...` — the latest calls.
- `GET /health` — liveness (public).
