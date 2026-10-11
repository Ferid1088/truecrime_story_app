# 0004 Record outgoing calls by wrapping httpx
**Context.** The app calls ~10 outside services from ~25 places. Cost and failures were invisible.
**Decision.** Wrap `httpx` `send()` once at start-up. Each call records host, path (never the query string), status, latency and, for LLM-style JSON, tokens and cost; bodies and headers are never read into the ledger. Writes go through a queue and one thread, and can never fail a call.
**Consequences.** New integrations are covered automatically as long as they use httpx. Streaming responses record no token counts. Cost is only what the provider reports.
