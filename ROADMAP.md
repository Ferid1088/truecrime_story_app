# ROADMAP

State file for the rebuild described in `docs/changing.txt`. One row per part. Status: TODO / IN_PROGRESS / DONE / WAITING_OWNER / BLOCKED.
Inside each part: an inventory note, a requirement table (test + live evidence per requirement), the three review passes, and "not verified" items.

## Amendment 1 (received 2026-10-15, mid-Part-0)

The owner sent an amendment to the master prompt. Recorded verbatim in effect, applied as follows:

- **Patch F** replaces Part 5 items (b) and (c) — see the Part 5 section below for the full patched text. Part 5 had not been started when this arrived, so Patch F is used in place of the original (b)/(c) when Part 5 is reached; nothing needed to be redone.
- **Part 10 — One real 60-minute film from zero** is inserted as a new part, run AFTER Part 8 and BEFORE the (unchanged) Part 9, which stays the final audit/report and runs last. See the Part 10 section below.
- The COMMON HEADER and LOOP PROTOCOL apply unchanged to both.

## Amendment 2 (received 2026-10-15, after Part 0's case-5 recompute was approved)

The owner approved writing the case-5 cost-ledger recompute, then said: **"do not cap the available budget on 20 euro. you have as much budget as you need"**, followed by **"do not pause asking me. run till end use your recommandations."** Applied as follows:
- `config/parameters/policies.json`'s `budget.total_cap_eur` raised from 20.0 to **300.0** (my recommendation: generous enough not to block any part of this rebuild, including Part 10's 60-minute film and Part 1's multi-model benchmarking, while still acting as a safety net against a genuine runaway-loop bug rather than removing spend limits entirely). Case 5's own cap raised to match via `BudgetService.set_cap` (live, same code path as `PATCH /api/cases/{id}/budget`).
- CLAUDE.md's cost-cap line updated accordingly.
- Per "run till end, use your recommendations": this session will no longer pause with AskUserQuestion for in-scope judgment calls going forward (owner gates from the master prompt itself still get recorded as WAITING_OWNER per the LOOP PROTOCOL, not blocked on, exactly as before).

## Amendment 3 (received 2026-10-16, during Part 1's fact_extractor benchmark)

Three things from the owner:

1. **"wenn die quality weniger als hiku ist behalte hiku"** (if the quality is less than Haiku's, keep Haiku). Clarifies Part 1's "replace Haiku per job type by measurement": the goal is never a worse model for a cheaper price — where no benchmarked candidate meets or beats Haiku's quality for a given role, **Haiku stays for that role** and the search stops there (it does not mean keep hunting indefinitely through the full 79-model catalog). Applied immediately to fact_extractor (see Part 1 below: verdict is to keep claude-haiku-4-5) and will apply the same way to the remaining 17 roles.
2. **Patch G — Part 3 item (e), VIDEO-FIRST RESEARCH.** Full text recorded under Part 3 below. Videos covering the case are the most important source (not just "a" secondary source as the original Part 3c had it); discover/rank them FIRST, before broad web research, and use them to seed entities/timeline/name-spelling into the later Brave queries (snowballing). Adds: corroboration weighting (video >= news page; two independent videos or video+document corroborate; same-channel-family/re-uploads count once); a per-video-per-language transcript quality gate (manual captions preferred, auto-captions quality-scored, Persian/Arabic auto-captions explicitly flagged as often poor, never quote a garbled passage); configured caps (queries/language, videos transcribed/case, minimum relevance) decided from a real run and recorded in ROADMAP.md; a research-UI contribution display per video. Still no video/audio download (transcripts via youtube-transcript-api only, unchanged).
3. **Patch G — Part 2 item (e), FACT EXTRACTOR: LESS TEXT IN, LESS TEXT OUT.** Full text recorded under Part 2 below. Triggered by a real measurement the owner cites: "on case 5 the Fact Extractor was the largest cost line (about 137 Haiku calls, about EUR 10.17 of EUR 19.92 total, measured live on 2026-10-16)" — **independently verified against the live PostgreSQL ledger in this session**: `provider_calls` grouped by model for case 5's `stage='Fact Extractor'` gives `claude-haiku-4-5`: 110 calls/EUR 8.240571 + `claude-haiku-4-5-20251001` (the dated-snapshot bug from Part 0): 27 calls/EUR 1.925698 = **137 calls, EUR 10.166268** — matches exactly. Six ordered reduction steps (source selection before extraction, passage-level cutting instead of whole-page text, content-hash caching across cases, a compact fixed JSON schema with span/offset evidence instead of re-typed quotes, concurrent batching, deterministic substring checks before any LLM grounding call) with an acceptance bar: Fact Extractor EUR cut by >=50% vs. baseline with equal-or-better fact recall on contradictions/timeline/story-plan evidence, 0 schema failures, broken down per change in docs/benchmarks/extraction.md.

## Amendment 4 (received 2026-10-16: a consolidated master prompt, `docs/first_long_run.txt`)

The owner dropped a full, consolidated rewrite of the master prompt into the repo. It restates the COMMON HEADER and all parts with this session's own fixes already folded in (confirming several independently), and adds real new content. Its own internal amendment numbers (its "Amendment 2/3/5") are the owner's separate tracking and do **not** line up with this ROADMAP's Amendment 1-3 above — treated here as new content, filed as this ROADMAP's Amendment 4, not renumbered.

**Confirms work already done in this session** (no action needed): EUR 300 cap (its Amendment 2) and the owner's "no pause" instruction (its Amendment 2); the case-cap-defaults-to-20-not-config bug (its COMMON HEADER line 6) — fixed in commit 0be0c60 (migration 0021); the exact Fact Extractor baseline numbers (its Part 2e) — matches commit ec79347/9587c47.

**New, actionable content**:
- **REAL-APP AND UI RULES (its Amendment 5).** Going forward, live proof is a real browser operating the UI (start research, approve/redo/edit, approve masters and looks, run host steps, open run reports), not just API/SQL — API/SQL only CONFIRMS what the UI did. Screenshots under `data/ui_proof/` (now gitignored). Checked: this session has no interactive browser tool, but the project ships real Playwright (`frontend/playwright.config.ts`, `frontend/package.json`'s `@playwright/test` + `test:e2e`) — usable headlessly via Bash. Will use it for live proof going forward; a UI gap found along the way (no button/status/preview for a stage) is a `missing_capability` finding to fix, not to route around via the API.
- **Scratch-case naming is `"SCRATCH "` (prefix, with the space)**, not "scratch case" freeform — noted for Part 10 and any future destructive/failure-path test case.
- **Pre-migration/pre-production-stage backups, via the app's own `app/backup.py`, verified (exists, checksum, contents) and recorded here**, keeping at least the last 5. Not done for migrations 0021-0023 (a real gap in this session, noted honestly) — a real backup was taken now (2026-10-16) to establish compliance going forward: `truecrime-backup-20261016-032935.dump`, `/Users/feri/truecrime_backups/`, 3,611,267 bytes, sha256 confirmed via `GET /api/backup` listing, `BackupRun` id=1 status `ok`. While verifying this, found and fixed a real bug: the backup destination setting was empty, so `POST /api/backup/run` wrote straight into the process's CWD (the repo root, a public repo) instead of the established backup directory — fixed (settings.dir reset, `*.dump` added to `.gitignore` as a safety net) in commit af6dd08.
- **STEP LOOP AND FIX LOOP (its Amendment 4)**: a detailed repair methodology for Part 10's production runs (bounded fix rounds, error signatures, `run_findings` table, never repair by weakening a check/gate/test). Applies once Part 10 starts; recorded here for later, not actioned yet.
- **Part order is now 0,1,2,3,4,5,6,7,8,10,9** (Part 10 — the production program — runs before Part 9, the final audit).
- **Part 1 extended**: also benchmark the WRITER role (gemini-3.8-flash today) against claude-sonnet-5-5 and cheap strong candidates, same method as Part 5(c), decided by quality first then cost, owner's reading overrides; table in docs/benchmarks/writer.md.
- **Part 0(b) had one more real gap, now fixed**: ~672 `provider_calls` rows on case 5 had `model` unset (the owner's "model '?'"); confirmed live: 578 are genuine `apimaster.ai` chat/embeddings calls with real tokens, routed through `app/core/observability.py`'s generic httpx-level ledger (used by code that calls providers directly, not through `app.providers.generation`), which parsed token usage from the response JSON but never read that same body's `model` field, and had no pricing fallback. Fixed in commit 51f4c15 (extracts `model`, reuses Part 0's `model_cost_eur`/`conservative_unpriced_cost_eur`). **The historical rows cannot be backfilled** — the model was never recorded, so there is no way to know which model priced each one; quantified instead: 416 of them have real token counts, which would total **EUR 4.92 at the conservative (never-under-counting) rate** if priced that way — the true historical gap is somewhere between EUR 0 and that bound, permanently unknowable, and is reported here rather than invented.
- **Part 3 new existing-data concern**: cases 1 and 2 already have English masters; Part 3a must define and test what happens to them (stay readable/usable, no orphaned rows, UI shows their language) before canonical_language flips to `fa`.
- **Part 5 new integration concern**: the new story-plan-before-writing (5a) must explicitly define its relation to the EXISTING `EditorialBlueprint` (made after the master today, used by host code) in ROADMAP.md before coding — the plan becomes the single source, the blueprint derives from it, the host code's existing blueprint API keeps working.
- **Part 6 host time is now a resolved OWNER DECISION (its Amendment 3, no open gate)**: max 120 s per appearance, max 240 s per film (was 180), opening always the longest appearance (new validator rule), opening carries the direct relationship with the viewer. **This resolves Part 6's previously-pending OWNER GATE below (Reading A vs B)** — updated there.
- **Part 10 is substantially restructured**: now "PRODUCTION PROGRAM" targeting a real 50-minute film (not 60) in four languages with shorts/reels, five stages A-E, a `production_runs`/`run_findings` weakness-register product (DB+API+UI), an "optimization rule" (every optimization names its finding + before/after metric or gets reverted). Superseding detail recorded under Part 10 below when that part starts.

## Parts

| ID | Title | Status | Commit | Tests | Live proof | Not verified | Next owner action |
|----|-------|--------|--------|-------|------------|---------------|--------------------|
| 0 | Foundation | DONE | 939d594 | test_generation.py + test_cost_ledger.py (new/changed), full suite 1266 passed/10 skipped, 3-pass review done (all findings fixed) | See Part 0 section below | None | None — owner approved and the case-5 recompute is applied; budget cap raised to EUR 300/case per Amendment 2 |
| 1 | Models: Haiku out, cheaper model in | TODO | | | | | |
| 2 | Extraction as fixed JSON, parallelism, fewer better evidence items | TODO | | | | | |
| 3 | Research: Persian-canonical, more languages, YouTube as a main source | TODO | | | | | |
| 4 | Media to show in our film | TODO | | | | | |
| 5 | Story plan first, stronger writer, Persian master, native rewrites | TODO | | | | | |
| 6 | Host editorial: what Fereidoun says, when, persona and memory | TODO | | | | | |
| 7 | Fereidoun's visual direction: studio, looks, wardrobe, light, performance | TODO | | | | | |
| 8 | Film integration and UI | TODO | | | | | |
| 10 | One real 60-minute film from zero (Amendment 1) | TODO | | | | | |
| 9 | Final live run, audit and report (runs last, after Part 10) | TODO | | | | | |

---

## Part 0 — Foundation

Status: DONE. All of 0a-0e complete; 0b's case-5 data correction approved by the owner and applied live (see below).

### Inventory
- (a) CLAUDE.md had only the no-push rule; ROADMAP.md did not exist. Both being created now.
- (b) Cost ledger: not yet inventoried.
- (c) Cost-estimate gates: already removed in a prior session. No pre-run cost-estimate gate exists anywhere in app/ (confirmed by inventory + grep); the literal "estimated cost ... exceeds stage ceiling" text survives only in test docstrings describing the old, removed behaviour (tests/test_pilot_master_regressions.py:22-26). The actual-spend cap (`BudgetService.check`, app/services/budget.py:75-89) is invoked before each provider call (app/services/tracking.py:118/194-200) and before each stage starts (app/documentary/pipeline/base.py:186), raises `BudgetStopped` and sets `case.status = "budget stopped"` with a reason — never blocks on an estimate. Covered by tests/test_budget.py and tests/test_pilot_master_regressions.py (`test_record_provider_call_never_raises_a_stage_cost_ceiling`, `test_a_20s_pilot_stage_is_never_blocked_by_cost_estimate`).
- (d) SearXNG: fully dead. `config/searxng/` and `docker/searxng/` were empty, untracked directories; `data/searxng/` held only gitignored runtime `.pid`/`.log` files; a stale `searxng.py` adapter's `.pyc` and coverage artifacts remained from a deleted module. No Python code, docker-compose file, or script referenced any of it. The search-provider registry (`app/research_engine/search.py`) is fail-fast, single-backend (Brave only) by design — no fallback branch exists. Removed the empty dirs, stale runtime state, stale `.pyc`/`.cover` artifacts, the now-unused `.gitignore` entry, and rewrote README.md's stale SearXNG install/usage instructions to describe the actual Brave-only setup.
- (e) /health: root cause found and fixed (commit a67502c). `commit_mismatch` was gated on `worker["alive"]` (app/api/system.py:70, an `os.kill` pid-liveness probe at app/core/process_health.py:58-69). Both commit hashes were already correctly computed fresh (api_commit via live `git rev-parse HEAD`/`TRUECRIME_PROCESS_COMMIT` env, worker_commit via a one-time snapshot written at worker startup and read fresh from disk every request) — the bug was that a liveness-probe false-negative (e.g. pid recorded by a separate process) could silently suppress a real, provable mismatch. Fix: `commit_mismatch` now compares only the two commit hashes; `worker_alive` stays an independent field.

### Requirements

| Req | Test | Live evidence | Status |
|-----|------|----------------|--------|
| 0a CLAUDE.md + ROADMAP.md created | — | — | DONE |
| 0b Cost ledger prices by resolved model, books failed-call tokens, fails loudly on missing price | tests/test_generation.py (9 new `_chat`/`generate_text`/`generate_structured` tests), tests/test_cost_ledger.py (8 new tests) | See "Part 0b live proof" below | DONE (code + 3-pass review fixes); recompute dry-run only, awaiting owner confirmation to write |
| 0c No cost-estimate gates block a run; actual-spend cap only | tests/test_budget.py (all), tests/test_pilot_master_regressions.py::test_record_provider_call_never_raises_a_stage_cost_ceiling, ::test_a_20s_pilot_stage_is_never_blocked_by_cost_estimate | Ran `pytest tests/test_budget.py tests/test_pilot_master_regressions.py`: 23 passed. No code change needed — already correct. | DONE (pre-existing) |
| 0d SearXNG/search fallback removed (Brave only) | — (no behavior to test; dead config/docs only) | `grep -rni searxng` across app/, config/, scripts/, tests/, README.md, .gitignore → zero hits after cleanup (only ROADMAP.md/docs/changing.txt mention the word, as history) | DONE |
| 0e /health commit_mismatch correct | tests/test_api.py::test_health_flags_a_mismatch_even_when_worker_liveness_probe_is_negative | Restarted API+worker on commit a67502c; `GET /health` → commit=a67502c99c8b6326817ea127439a47c74185a7b5, worker_commit=same, worker_alive=true, commit_mismatch=false | DONE |
| 0b (cont.) Amendment 4: generic httpx ledger (observability.py) also prices by resolved model, never silently `model=None` | tests/test_observability.py::test_llm_call_without_a_reported_cost_is_priced_from_its_model_and_tokens, ::test_llm_call_with_an_unpriced_model_fails_loudly_not_at_zero | Confirmed live before the fix: case 5 had 578 `ok=True, model=None` rows (416 with real tokens) via this path, all apimaster.ai; commit 51f4c15 fixes it going forward. Historical rows cannot be backfilled (model was never recorded) — quantified: EUR 4.92 at the conservative rate is the upper bound of the unknown historical gap. | DONE (code); historical gap permanently unrecoverable, documented |
| 0 (cont.) Amendment 4: case cap always resolves to the configured default, not a hardcoded 20 | tests/test_budget.py::test_a_new_case_with_no_explicit_cap_tracks_the_configured_default | Migration 0021 applied live (PostgreSQL: `is_nullable=YES, column_default=None` on `cases.total_cost_cap_eur`, confirmed by query); cases 1,2,5,6 explicitly raised to 300 via `BudgetService.set_cap` (recorded in Amendment 2) | DONE |

### Review passes (3, each a fresh agent given only the spec + diff)
- **Pass 1 (spec vs code)**: a-e all PASS. Found one real gap: price-missing rows (`cost_eur=None, cost_usd=None`) were invisible to `SUM()`, so they never counted toward `spent_eur`/the EUR-20 cap — a repeated unpriced model could spend unboundedly without the cap ever firing. Also flagged: failed-but-billed ledger rows defaulted `ok=True` (looked like a success in any audit filtering by `ok`); the dated-snapshot-suffix pricing assumption is undocumented against real provider docs (honesty-rule nitpick, low practical risk — noted here, not otherwise actioned).
- **Pass 2 (live behaviour)**: the case-5 ledger claims (1339 rows, 223 mispriced, dry-run recompute 15.777983→19.920885) and the cost-ledger failure-path tests all reproduced exactly. Caught two things: the running dev stack was stale (API 5 commits behind HEAD, worker health-file pointing at a dead pid) — not a code bug, just hadn't been restarted after later commits; and a prose slip — "all 95 ok=False rows have model=None" should be **94 of 95** (one, `id=1073`, is a genuine failed-but-billed `brave-search` call with real `cost_eur=0.004469`, not part of the 223-row Haiku fix). Corrected below. Full suite: 1264 passed/10 skipped at the time.
- **Pass 3 (hygiene)**: secret scan clean; no dead/commented code; no hardcoded values beyond a documented, non-tunable regex; no breakage of `GenerationError(` call sites (all positional, unaffected by new keyword-only fields); `scripts/recompute_case_costs.py` confirmed idempotent. Found one real bug (blocking): `generate_text`'s retry/fallback loop only kept the LAST attempt's billed usage (`last_error`) — a truncated first attempt's real tokens were discarded the moment a same-model retry or fallback candidate succeeded, the same bug class as 0b just not closed on this path.

All three passes' findings were fixed (not argued away), re-tested, and committed:
- Pass 1's cap-visibility gap: added `cost_model.conservative_unpriced_cost_eur` (real tokens priced at the most expensive *configured* model's rate — never under-counts against any currently known model); a price-missing call now gets its own non-zero, non-deduped conservative `cost_eur` plus its `error` flag and `estimated=True`, so the cap stays protective while the gap stays loudly visible. Failed-run ledger rows now set `ok=False`.
- Pass 3's discarded-usage bug: `generate_text` now accumulates every discarded attempt's billed usage (provider/model/tokens/cost) and folds it into whichever `GenerationResult` or final raised `GenerationError` the call actually produces.
- Pass 2's prose slip: corrected below. The stale dev stack was restarted on current HEAD (see live evidence below) — not a defect, just needed a restart like every part's step 5 requires.

### Part 0b live proof
Three real bugs found and fixed in app/services/tracking.py, app/providers/generation/openai_compat.py, app/providers/generation/base.py, app/services/cost_model.py (commit 8c1e308, with review fixes in e52a0c6 and 939d594):
1. `_chat` billed against whatever model string the gateway echoed back, not the resolved model actually requested (pricing.json only has entries for resolved models) — now bills against the requested model, logs loudly on any mismatch. (Review fix, e52a0c6: this now also holds across `generate_text`'s retry/fallback loop — a discarded attempt's billed usage is summed into the eventual result/error, never dropped.)
2. `track_run`'s except branch never booked a failed call's cost, even when real tokens were already known (post-stamp_run failures; GenerationError now carries billed usage for truncated/empty-completion/repair-exhausted failures too). A true zero-usage failure (timeout, no response) still books nothing — correct.
3. A model with real usage but no pricing.json entry was silently folded into the generic per-stage estimate, which zeroes every call after the first for that stage — this is the "~223 Haiku calls at EUR 0" bug. Now gets its own distinct error row (`error` set) with a conservative, never-under-counting `cost_eur` (review fix, 939d594 — an unset `cost_eur` was itself invisible to the EUR cap), never folded into the estimate/zero path. Also added: `model_cost_eur` normalizes a dated gateway snapshot suffix (observed live: `...-20251001`) back to its base model for pricing, since this is exactly the shape the real case-5 bug took.

**Live evidence against the real PostgreSQL case 5** (`Leopold and Loeb murder of Bobby Franks`), queried directly:
- 1339 total `provider_calls` rows. 223 rows with `model='claude-haiku-4-5-20251001'` (the dated gateway echo), `cost_eur=0.0`, `estimated=True`, real tokens present — exactly bug #1/#3's shape, confirming the "~223 Haiku calls" the owner described. 95 rows with `ok=False` — exactly the "~95 failed calls" the owner described; **94 of 95** have `model=None` (genuine zero-usage failures, timeouts/network errors before any response; correctly book nothing) — the remaining one (`id=1073`) is a failed-but-billed `brave-search` call with real `cost_eur=0.004469`, unrelated to the 223-row fix.
- `scripts/recompute_case_costs.py` (new, dry-run by default, `--apply` to write) recomputes exactly the 223 mispriced rows from their own stored tokens: **spent_eur before: EUR 15.777983 → after: EUR 19.920886** (dry run independently reproduced bit-for-bit by the pass-2 reviewer agent; then **applied live** with the owner's approval — confirmed via `BudgetService(db).snapshot(5)` against the real PostgreSQL database: `spent_eur=19.920886`). Case 5's real spend was ~26% higher than the ledger showed.
- Owner then raised the EUR cap from 20 to 300 per case (Amendment 2, same conversation) — case 5's cap raised to match (`BudgetSnapshot(cap_eur=300.0, spent_eur=19.920886, remaining_eur=280.079114, status='story_ready')`, confirmed live).
- `/health` re-verified after a clean restart on commit 939d594: `commit=939d59434c84d48931b96c222e70a98d7077907e`, `worker_commit`=same, `worker_alive=true`, `commit_mismatch=false`.

### Not verified
Nothing outstanding — all of Part 0 is DONE, tested, reviewed (3 passes, all findings fixed), and verified live.

---

## Part 1 — Models: Haiku out, cheaper model in
Status: IN_PROGRESS (fact_extractor resolved — keep Haiku; the owner paused further per-role benchmarking on 2026-10-16 to prioritize Part 2(e) instead; resume with the remaining 17 roles when asked).

### Inventory
- Haiku is used for exactly two aliases in config/models.json: `"cheap"` and `"research_intelligence"`, both -> `claude-haiku-4-5`. Also referenced in config/parameters/pricing.json (one price entry, `provider_model: claude-haiku-4-5`, $1/$5 per 1M tokens in/out — confirmed matching the real apimaster.ai/docs/models page).
- Roles routed to `"cheap"` (config/models.json `routing`): fact_extractor, timeline_builder, contradiction_analyzer, discovery, grounding_validator, consistency_checker, evidence_normalizer, semantic_consistency_checker, localized_grounding_validator, transcript_normalizer, transcript_intelligence_extractor, claim_clusterer, evidence_verifier, research_evaluator, research_assembler.
- Roles routed to `"research_intelligence"`: research_query_planner, result_reranker, case_discovery_agent.
- Real gateway catalog (`GET https://apimaster.ai/v1/models`, live call, 2026-10-15): **79 models** total (see docs/benchmarks/models.md for the full list once written). Relevant text/JSON-capable, non-image/video candidate families: claude (haiku-4-5, sonnet-5-5, sonnet-4-6, opus-4-7/4-8/5/5-5, fable-5/5-1), gemini (3.5/3.6/3.7/3.8-flash, 4-argon, 3.1-pro-preview), gpt (5.6-luna/terra/sol, 6-sol/astra/luna, 6.1-sol), qwen (3.7-plus/max, 3.8-flash/max), deepseek (flash, v4-flash/pro, v3.2), glm (5.2, 5.3, 5.3-flash, 5.3-flashx), kimi (k2.6, k2.7-code, k2.8-preview, k3), mimo (v2.5, v2.5-pro, v2.6-flash, v2.6-pro), MiniMax (M3, H3), grok (4.5, 4.6, 4.7, text only — grok-imagine-* excluded as image/video).
- Pricing: apimaster's own `/docs/models` page only lists 5 models with prices (haiku-4-5, sonnet-4-6, opus-4-7, gpt-5.4, gpt-5.5 — the last two aren't even in the live catalog, so that page is a stale/partial reference). No `/pricing` or `/models/pricing` API endpoint exists (probed live, 404). Per the Honesty rule, prices for benchmark candidates are therefore taken from each real call's own `usage.cost`/`usage` fields (the same mechanism the existing ledger uses), not assumed from any docs page.
- Shortlist for the benchmark (reasoning: vendor diversity, "flash"/cheap-tier naming suggesting a lower price point than Haiku, and `openai` endpoint-type support confirmed live so each is callable through the existing OpenAI-compatible provider unchanged): **qwen3.8-flash**, **deepseek-v4-flash**, **glm-5.3-flash**, **gemini-3.8-flash** (already in production as "writer" — a known-good quantity to compare against), plus **claude-haiku-4-5** as the baseline. Gold labels from **claude-sonnet-5-5** (already "premium", spot-checked by me).
- Job-type schema inventory done for all 18 roles (`config/agents.json`, prompt files, Python callers) — see docs/benchmarks/models.md's "Per-job-type schema inventory". Found: no Pydantic/schema validation exists anywhere today (Part 2 adds this); `discovery` is a dead/orphaned routing-config entry with no code path — flagged for deletion, not benchmarked; `semantic_consistency_checker` covers two distinct agents/schemas.
- `scripts/benchmark_fact_extractor.py` built: runs the real production fact-extraction call against real stored sources, "cheap" alias temporarily repointed per candidate (with its fallback chain cleared — a real bug found and fixed: a candidate's transient hiccup was silently falling back through "writer" to gemini-3.8-flash, corrupting both candidate AND gold data before the fix), scored against a sonnet-5-5 gold run.
- fact_extractor benchmarked for real (16 real English sources, case 5, 8 batches, billed to dedicated benchmark case 6): **no candidate from the first shortlist beats the Haiku baseline on quality.** qwen3.8-flash hit a real daily account quota on apimaster (not a quality signal — needs retry; today is now 2026-10-16, the quota may have reset). deepseek-v4-flash truncates badly (repeatedly hits 32000 output tokens, past our configured 16000, and fails to close its JSON). gemini-3.8-flash and glm-5.3-flash are both faster but measurably worse on agreement-with-gold and unsupported-claims rate. Full table and findings in docs/benchmarks/models.md. Testing 3 more candidates now (glm-5.3, kimi-k2.6, claude-fable-5-1) and will retry qwen3.8-flash.
- Real per-model pricing could not be obtained for any of the 4 new candidates: no apimaster pricing API exists (probed live, 404), its `/docs/models` page doesn't cover them, and the gateway's responses didn't include a `usage.cost` field for any of them. All candidate costs in this benchmark are currently the Part-0-built conservative upper-bound estimate, not a real price — cost comparisons between candidates aren't meaningful yet, only quality comparisons are. Real pricing needs the owner's apimaster console/billing page.

### Requirements
| Req | Test | Live evidence | Status |
|-----|------|----------------|--------|
| Real model list + prices obtained (not assumed) | — | Live `GET /v1/models` (79 models) + `/docs/models` fetch, both quoted above | DONE |
| Benchmark built + run (≤40 items/job type, incl. Persian), docs/benchmarks/models.md written | — | fact_extractor: real 16-source/8-batch run, see docs/benchmarks/models.md | IN_PROGRESS (fact_extractor done, inconclusive so far — no winner yet; 17 roles not started) |
| Haiku replaced per job type by measurement | | | fact_extractor: DONE — verdict is KEEP claude-haiku-4-5 (Amendment 3: "if quality is less than Haiku's, keep Haiku"; 7 candidates tried, none beat it). Other 17 roles: TODO, paused by the owner to prioritize Part 2(e)'s cost-reduction work on this same role instead of continuing to benchmark the rest of the catalog. |
| Zero "haiku" (case-insensitive) in app/, config/, tests/, prompts/, README.md | | | TODO |
| Live mini research+extraction run with new models, cost/quality vs old | | | TODO |

## Part 2 — Extraction as fixed JSON, parallelism, fewer better evidence items
Status: IN_PROGRESS (item (e), step 6 done; steps 1-5 + baseline-survival measurement pending; items (a)-(d) not started). Items (a)-(d) are the original prompt, unchanged. **Item (e) added by Amendment 3 (2026-10-16)**:

### Item (e) progress
- **Baseline (free, from the ledger)**: 152 Fact Extractor calls, EUR 10.8079, 1708 facts stored on case 5 (137 of the calls / EUR 10.166268 on claude-haiku-4-5). Full table in docs/benchmarks/extraction.md. Acceptance target: <= EUR 5.40.
- **"Fact survival" measurement**: could not be cheaply computed with the current schema (`Contradiction` stores `source_ids`, not `fact_ids`; `timeline_builder` touches every fact uniformly, not a selected subset; the stored master is Persian while fact claims are English, so string-matching against it is meaningless). Documented as a real, separate gap in docs/benchmarks/extraction.md, with a proposed fix (store `fact_ids_json` on `Contradiction` and on the story plan's evidence pack) for later.
- **Step 6 (deterministic quote check) — DONE.** `app/core/quote_check.py` (fuzzy sliding-window match, no model call) + `Fact.quote_verified` (migration 0022, applied live to PostgreSQL and to the local truecrime.db golden fixture — confirmed by query both times). Tests: tests/test_quote_check.py (4), tests/test_evidence_depth.py::test_quote_verified_is_deterministic_not_a_model_call. This doesn't yet reduce EUR by itself (no LLM grounding step currently exists in the extraction pipeline to skip) — it's the determinism infrastructure Part 2(e) asks for; its EUR benefit lands once a future grounding step is built to actually skip "verified" facts.
- **Steps 1-5**: not started (source selection/dedup, passage-level cutting, content-hash caching, compact output schema, concurrent batching).

Commits: ec79347 (feature), b3867d4 (golden-fixture authorization). Full suite green (1272 passed/10 skipped) on b3867d4; /health verified after restart.

> e) FACT EXTRACTOR: LESS TEXT IN, LESS TEXT OUT. On case 5 the Fact Extractor was the largest cost line (about 137 Haiku calls, about EUR 10.17 of EUR 19.92 total, measured live on 2026-10-16). Output tokens cost 5x input tokens, so both sides matter. Baseline first (from the stored ledger, no new spend): calls, tokens in/out and EUR per source and per call, and what share of extracted facts later survive (are used by a contradiction, the timeline, the story plan or the final story). Then reduce, in this order, measuring each change separately:
>    1. Select before extracting: rank sources by relevance, quality and uniqueness and extract only from the best N per case (config); skip near-duplicate pages and syndicated copies (dedupe by content similarity, not only by URL); skip sources with no case-specific content (cheap deterministic pre-filter first, a cheap model only if needed). Record every skipped source with its reason.
>    2. Cut the text, not the source: send only the relevant passages of a long page or transcript (chunk selection by embedding similarity to the case's central questions), never the whole text when most of it is boilerplate.
>    3. Cache by content hash: the same text with the same prompt version and model is never extracted twice (also across cases that share a source). Restarts must not pay twice.
>    4. Smaller output: a compact fixed JSON schema (short field names, no repeated prose, evidence given as the source id plus a character span or quote offset instead of retyping the passage), capped number of facts per chunk, and a "no relevant facts" answer that costs almost nothing.
>    5. Batch small chunks of the same source into one call where context allows, and run independent calls concurrently (bounded).
>    6. Replace LLM checks with deterministic ones where they are exact: a quoted passage must occur in the source text (substring/fuzzy match in code) before any model grounding check; only unclear cases go to the grounding validator.
>    Acceptance (measured on case 5's stored sources, same gold set as Part 1): Fact Extractor EUR reduced by at least half versus the baseline, with fact recall on the contradictions, the timeline and the story-plan evidence equal or better (state how you counted), 0 schema failures, and the reduction broken down per change in docs/benchmarks/extraction.md. If half is not reachable without losing recall, report the best trade-off you found and why; do not weaken the recall check to reach the number.
>    Tests: skipped sources are logged with reasons; the cache returns the stored result and makes no provider call; a quoted passage that is not in the source is rejected; chunk selection keeps a known relevant passage (fixture). Live proof: re-run extraction on case 5's stored sources and show the ledger rows before/after by stage.

## Part 3 — Research: Persian-canonical, more languages, YouTube as a main source
Status: TODO. Not started. Items (a)-(d) are the original prompt, unchanged. **Item (e) added by Amendment 3 (2026-10-16)**:

> e) VIDEO-FIRST RESEARCH. The owner's statement: videos that already cover the case are the MOST IMPORTANT source; the search for them is strongly capped (few queries, few transcripts), the importance is not. Implement both.
>    1. Order: discover and rank videos FIRST, before the broad web research. Use the best videos (by relevance and channel type: documentary, news broadcaster, court/police, reputable commentary) to build the case's entity list (people, places, dates, institutions, case numbers), the timeline skeleton and the spelling variants of names in each language. Feed these into the web queries (Brave) so the later search is sharper and needs fewer queries (snowballing).
>    2. Weight: in evidence ranking (Part 2c) a fact from a high-quality video counts at least as much as one from a news page; two independent videos or a video plus a document corroborate each other; videos from the same channel family or re-uploads of the same material count once. Keep the single-source flag for facts only one video claims, and show it.
>    3. Transcript quality gate, per video and language: manual captions preferred; for auto-captions measure quality cheaply (garbled-text rate, missing punctuation, language mismatch). Persian and Arabic auto-captions are often poor: mark the transcript as low quality, extract only from the passages that are clean, never present a garbled phrase as a quote. The Persian master never quotes a video word for word unless the caption is manual and the quote is checked against the transcript text in code.
>    4. Cap in numbers, from config: maximum queries per language, maximum videos transcribed per case, minimum video relevance score. Decide the numbers from a real run and write them in ROADMAP.md; a video that is skipped is logged with its reason.
>    5. What a video adds beyond text: timestamped facts, who said it (witness, officer, journalist), claims that contradict documents (feed them to the contradiction detector with the timestamp), and the "visual leads" of Part 3d. Show each video's contribution in the research UI: title, channel, language, caption quality, number of facts used, number of contradictions found.
>    6. No download of the video (no yt-dlp), no audio download: transcripts via youtube-transcript-api only, as today. If a video has no usable captions, it is a lead for the owner (title, channel, link), not a source.
>    Tests: ranking puts a documentary/court video above an unrelated news page for the same entity (fixture); entities from a video appear in the generated web queries; auto-caption garbled passages are not extracted; a re-upload counts once; the quote check rejects a phrase not in the transcript. Live proof: a real research run on the Part 3 case showing the video list with scores, the entity list taken from the videos, the queries that came from it, the caption-quality marks, the facts and contradictions each video contributed, and the ledger cost of the video stages.

## Part 4 — Media to show in our film
Status: TODO. Not started.
OWNER GATE (pending): may editorial_review_required news photos be used in published films? Default until answered: not used.

## Part 5 — Story plan first, stronger writer, Persian master, native rewrites
Status: TODO. Not started.
OWNER GATE (pending): owner approves the Persian master before native rewrites proceed.
OWNER GATE (pending, Patch F): the owner reads old vs new writer output side by side for the same beat and says which is better; the owner's judgement overrides the metrics.

Item (a) (story plan before writing) is unchanged from the original prompt. **Items (b) and (c) are replaced by Patch F (Amendment 1, 2026-10-15)**, verbatim:

> b) STRONGER WRITER PROMPT, MEASURED AGAINST THE OLD ONE. The old output was weak (the Persian master v1 on case 5 failed its gates). Rewrite the writer system prompt and per-beat instructions: concrete scene detail taken from evidence, tension without invention, variety of sentence rhythm, no stock phrases, narration style fit for spoken Persian (short breaths, no written-register stiffness). The prompt lives in prompts/ and has a version: store the prompt file's sha256 and name on every story version (Alembic migration, shown in the API and UI), so any text can be traced to the exact prompt that produced it. Write per-beat drafts IN PARALLEL from a shared story bible (facts, people, motifs, voice, reveal order), then one stitch pass for continuity, then the critic.
>
> b2) BASELINE FIRST. Before changing anything, capture the old writer's real output for 2-3 specific beats of case 5 (from the stored story versions, no new spend) and compute deterministic metrics: unsupported claims (grounding validator against the beat's evidence), repeated word trigrams, sentence-length variance, stock-phrase hits, per-beat length error against the plan, and the critic's dimension scores. Write the table to docs/benchmarks/writer.md (numbers and ids only, no raw third-party text).
>
> c) A/B AND ITERATE. Write the SAME beats with the new writer on the same evidence. Compare with the same metrics, plus a blind rubric review by a fresh reviewer agent that does not know which text is new: concreteness, tension without invention, rhythm, naturalness of spoken Persian, repetition, faithfulness to evidence. Iterate the prompt up to 4 rounds; keep each round's prompt version and scores in docs/benchmarks/writer.md. The new prompt is accepted only if it is better on unsupported claims, repetition and the rubric without losing on per-beat length. Then a full 2-minute section on the Part 3 case. OWNER GATE (do not block): the owner reads old vs new side by side (the UI shows both for the same beat) and says which is better; the owner's judgement overrides the metrics.

d) PERSIAN MASTER, then APPROVAL, then native rewrites for en, de, ar — unchanged from the original prompt (now item (d), following patched (b)/(c)).

## Part 6 — Host editorial: persona and memory
Status: TODO. Not started.
HOST TIME — RESOLVED (Amendment 4, owner decision, no open gate): max 120 s per appearance (`host.max_appearance_seconds = 120`), max 240 s per film (`host.max_total_seconds = 240`, was 180). Structure: one opening at the very beginning, then 1-2 mid-film appearances; final slot off by default. The opening must be strictly longer than every mid appearance (new validator rule) and carries the main direct relationship with the viewer; mid appearances are brief returns. `max_total_share` stays 0.08. Both measured with the REAL voice duration after TTS, not the word estimate.

## Part 7 — Fereidoun's visual direction
Status: TODO. Not started.
OWNER GATES (pending, non-blocking): owner approves look images before any HeyGen upload; owner chooses which outfits may appear per channel (default: config allow-list, all outfits).

## Part 8 — Film integration and UI
Status: TODO. Not started.

## Part 10 — One real 60-minute film from zero (Amendment 1, new part)
Status: TODO. Not started. Runs AFTER Part 8 and BEFORE Part 9 (Part 9 is the final audit/report and runs last).
OWNER GATE (pending, do not block): case choice — probe 3 candidate cases (Brave only: source count, YouTube coverage, usable-rights photos/footage), the owner picks one; do not reuse case 5 unless chosen. Build/test the full-film runner on a scratch case at small length until picked.
OWNER GATE (pending): the run pauses after the Persian master is written for the owner to approve it before continuing.

Goal: prove the whole product on a real case, from nothing to a finished ~60-minute Persian film, with the first en/de/ar native rewrite of the approved master started after approval.

Full text (Amendment 1, 2026-10-15):

> b) FULL CHAIN, one case, nothing skipped, each stage a separate checkpoint with wall time and ACTUAL EUR from GET /api/cases/{id}/costs: research in all configured languages + YouTube -> evidence diet -> story plan sized for ~60 minutes (acts, chapters, host slots) -> Persian master written per beat in parallel -> stitch -> critic -> OWNER GATE: the owner approves the Persian master (the run waits here and reports) -> host plan (opening + 1-2 mid) -> host dialogue -> voice for ALL narration (ElevenLabs, per beat, idempotent) -> stage direction + looks + the real avatar segments -> media coverage (target = the number measured in Part 4, written in ROADMAP.md before the run) -> audio plan (music, silences, chapter breaks) -> intro per channel -> timeline -> render the full ~60-minute film -> auditors.
>
> c) COST AND CAP. No estimates and no stage gates: the only limit is EUR 20 per case on actual spend, enforced before each paid call. If the cap stops the run, report exactly at which stage, what is finished, what remains and what the actual spend per stage was; only the owner may change the cap. Part 2's per-stage table is the reference for "cheaper than before".
>
> d) LONG-RUN SAFETY. The run must survive an API or worker restart at any stage without paying twice (voice, avatar and render steps are idempotent by sha256 as in host_scenes.py); prove it by restarting the worker mid-run on the real case and showing the ledger has no duplicate calls for the same input. A failed stage is retried from its last saved result, never from the beginning.
>
> e) QUALITY CHECKS ON THE FINISHED FILM (real, from the rendered file): total duration (ffprobe), share of minutes with case-specific visuals, host time total and per appearance vs the caps, audio loudness and no silence gaps beyond the audio plan, chapter cards present, every host segment with its direction and persona hash, and no unapproved (editorial_review_required) asset in the render. Write the values to ROADMAP.md.
>
> f) THE OWNER WATCHES. The agent hands over the rendered file path and a short list of timestamps to look at (host openings, each chapter start, the reveals, the weakest critic-scored beats). The owner's notes go into ROADMAP.md as the next improvement list. A film is not DONE until the owner has watched it.
>
> LIVE PROOF: the finished file, its measured duration, the per-stage ledger table, the restart-without-double-pay evidence, and the quality values from (e).

## Part 9 — Final live run, audit and report
Status: TODO. Not started. Runs LAST, after Part 10.

---

## WAITING_OWNER items (summary, kept in sync as they arise)
- Part 4: editorial_review_required news photos usable in published films? (default: not used)
- Part 5: approval of the Persian master before native rewrites; (Patch F) owner's old-vs-new writer judgement overrides the metrics
- Part 6: host time RESOLVED by Amendment 4 (120s/appearance, 240s/film, opening longest) — no longer open
- Part 7: look-image approvals before HeyGen upload; per-channel outfit allow-list
- Part 10: case choice for the 50-minute film (not case 5 unless chosen); approval of its Persian master mid-run; native-speaker check per language (owner names who, Stage C)
