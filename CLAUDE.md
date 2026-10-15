# Project rules

- **No push without an explicit owner request.** Commit locally as often as needed; `git push` (of a branch or a tag) only when the owner asks for it in this task, and only what they named. The repository is public.
- **Work live.** Real stack (API 8001, UI 3001, worker), real providers, real data. No isolated E2E stack or mocks for live proof; unit tests may use fakes.
- **"Verified" = verified live.** Every requirement needs an automated test (file::name) AND live evidence (the exact API/SQL call and result). Fakes/mocks/SQLite don't count as live proof for persistence, migrations or provider behaviour. Run every Alembic migration on live PostgreSQL and show the new column/table with a query.
- **Cost cap: EUR 300 per case (raised from EUR 20 by explicit owner instruction, 2026-10-15), actual spend only** (`GET /api/cases/{id}/costs`, `config/parameters/policies.json`'s `budget.total_cap_eur`). No cost-estimate gates that block a run. Stop cleanly before a paid call if `spent_eur >= cap_eur`. The owner may raise it further on request; still never raise it on your own initiative beyond what's asked.
- **Search: Brave only.** No OpenRouter, no Docker, no Finglish, no Haiku.
- **UI language: English only. Main branch only.**
- **Secrets:** never commit keys, tokens, `.env`, cached third-party pages or private data. Run `scripts/secret_scan.py` before every commit batch.
- **Code:** clean OO, no dead code, no commented-out code. Every bug fix gets a test. Every new config value goes into `config/`. Every new DB column/table gets an Alembic migration.
- **Honesty:** never claim something you did not run. Distinguish "verified live" / "verified by test" / "not verified". Don't assume provider capabilities — read the provider's real docs/API response first.
- **Languages:** research runs in all configured languages; Persian is the output language. A case with zero Persian sources is valid.
- **Destructive/failure-path testing** uses scratch cases, never case 5 or other real cases.
