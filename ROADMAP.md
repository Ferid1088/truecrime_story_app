# ROADMAP

State file for the rebuild described in `docs/changing.txt`. One row per part. Status: TODO / IN_PROGRESS / DONE / WAITING_OWNER / BLOCKED.
Inside each part: an inventory note, a requirement table (test + live evidence per requirement), the three review passes, and "not verified" items.

## Parts

| ID | Title | Status | Commit | Tests | Live proof | Not verified | Next owner action |
|----|-------|--------|--------|-------|------------|---------------|--------------------|
| 0 | Foundation | WAITING_OWNER | 8c1e308 | 59 new/changed (test_generation.py, test_cost_ledger.py), full suite 1262 passed/10 skipped | See Part 0 section below | Part 0b case-5 recompute not yet written to the DB | Approve `python -m scripts.recompute_case_costs 5 --apply` (or decline/ask for something different) — case 5's real spend is EUR 19.92, not the EUR 15.78 the ledger currently shows |
| 1 | Models: Haiku out, cheaper model in | TODO | | | | | |
| 2 | Extraction as fixed JSON, parallelism, fewer better evidence items | TODO | | | | | |
| 3 | Research: Persian-canonical, more languages, YouTube as a main source | TODO | | | | | |
| 4 | Media to show in our film | TODO | | | | | |
| 5 | Story plan first, stronger writer, Persian master, native rewrites | TODO | | | | | |
| 6 | Host editorial: what Fereidoun says, when, persona and memory | TODO | | | | | |
| 7 | Fereidoun's visual direction: studio, looks, wardrobe, light, performance | TODO | | | | | |
| 8 | Film integration and UI | TODO | | | | | |
| 9 | Final live run, audit and report | TODO | | | | | |

---

## Part 0 — Foundation

Status: WAITING_OWNER (0a, 0c, 0d, 0e DONE; 0b's code fix DONE, its case-5 data correction awaits owner approval — see below). Proceeding to Part 1 in the meantime per protocol.

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
| 0b Cost ledger prices by resolved model, books failed-call tokens, fails loudly on missing price | tests/test_generation.py (6 new `_chat`/`generate_structured` tests), tests/test_cost_ledger.py (7 new tests) | See "Part 0b live proof" below | DONE (code); recompute dry-run only, awaiting owner confirmation to write |
| 0c No cost-estimate gates block a run; actual-spend cap only | tests/test_budget.py (all), tests/test_pilot_master_regressions.py::test_record_provider_call_never_raises_a_stage_cost_ceiling, ::test_a_20s_pilot_stage_is_never_blocked_by_cost_estimate | Ran `pytest tests/test_budget.py tests/test_pilot_master_regressions.py`: 23 passed. No code change needed — already correct. | DONE (pre-existing) |
| 0d SearXNG/search fallback removed (Brave only) | — (no behavior to test; dead config/docs only) | `grep -rni searxng` across app/, config/, scripts/, tests/, README.md, .gitignore → zero hits after cleanup (only ROADMAP.md/docs/changing.txt mention the word, as history) | DONE |
| 0e /health commit_mismatch correct | tests/test_api.py::test_health_flags_a_mismatch_even_when_worker_liveness_probe_is_negative | Restarted API+worker on commit a67502c; `GET /health` → commit=a67502c99c8b6326817ea127439a47c74185a7b5, worker_commit=same, worker_alive=true, commit_mismatch=false | DONE |

### Review passes
Not yet run (part in progress).

### Part 0b live proof
Three real bugs found and fixed in app/services/tracking.py, app/providers/generation/openai_compat.py, app/providers/generation/base.py, app/services/cost_model.py (commit 8c1e308):
1. `_chat` billed against whatever model string the gateway echoed back, not the resolved model actually requested (pricing.json only has entries for resolved models) — now bills against the requested model, logs loudly on any mismatch.
2. `track_run`'s except branch never booked a failed call's cost, even when real tokens were already known (post-stamp_run failures; GenerationError now carries billed usage for truncated/empty-completion/repair-exhausted failures too). A true zero-usage failure (timeout, no response) still books nothing — correct.
3. A model with real usage but no pricing.json entry was silently folded into the generic per-stage estimate, which zeroes every call after the first for that stage — this is the "~223 Haiku calls at EUR 0" bug. Now gets its own distinct error row (`error` set, `cost_eur` left unset), never folded into the estimate/zero path. Also added: `model_cost_eur` normalizes a dated gateway snapshot suffix (observed live: `...-20251001`) back to its base model for pricing, since this is exactly the shape the real case-5 bug took.

**Live evidence against the real PostgreSQL case 5** (`Leopold and Loeb murder of Bobby Franks`), queried directly:
- 1339 total `provider_calls` rows. 223 rows with `model='claude-haiku-4-5-20251001'` (the dated gateway echo), `cost_eur=0.0`, `estimated=True`, real tokens present — exactly bug #1/#3's shape, confirming the "~223 Haiku calls" the owner described. 95 rows with `ok=False` — exactly the "~95 failed calls" the owner described; all 95 have `model=None` (no usage was ever stamped — genuine zero-usage failures, timeouts/network errors before any response; correctly book nothing).
- `scripts/recompute_case_costs.py` (new, dry-run by default, `--apply` to write) recomputes exactly the 223 mispriced rows from their own stored tokens: **spent_eur before: EUR 15.777983 → after: EUR 19.920885** (dry run; not yet written — see OWNER ACTION below). Case 5's real spend was ~26% higher than the ledger showed, 8 cents from the EUR 20 cap.
- OWNER ACTION (does not block the loop): writing this correction to the live case 5 rows needs the owner's go-ahead (the Claude Code auto-mode classifier itself declined to write to the shared production database without explicit permission) — the owner compares the new total with the gateway's own usage page, then either approves `python -m scripts.recompute_case_costs 5 --apply` or asks for something different.

### Not verified
Everything except 0a, 0c, 0d, 0e, and 0b's code fix. The case-5 recompute is dry-run only until the owner approves writing it.

---

## Part 1 — Models: Haiku out, cheaper model in
Status: TODO. Not started.

## Part 2 — Extraction as fixed JSON, parallelism, fewer better evidence items
Status: TODO. Not started.

## Part 3 — Research: Persian-canonical, more languages, YouTube as a main source
Status: TODO. Not started.

## Part 4 — Media to show in our film
Status: TODO. Not started.
OWNER GATE (pending): may editorial_review_required news photos be used in published films? Default until answered: not used.

## Part 5 — Story plan first, stronger writer, Persian master, native rewrites
Status: TODO. Not started.
OWNER GATE (pending): owner approves the Persian master before native rewrites proceed.

## Part 6 — Host editorial: persona and memory
Status: TODO. Not started.
OWNER GATE (pending): confirm host time — Reading A (120s/appearance, 180s/film total, default built) vs Reading B (120s total for the whole film, per-appearance cap dropped).

## Part 7 — Fereidoun's visual direction
Status: TODO. Not started.
OWNER GATES (pending, non-blocking): owner approves look images before any HeyGen upload; owner chooses which outfits may appear per channel (default: config allow-list, all outfits).

## Part 8 — Film integration and UI
Status: TODO. Not started.

## Part 9 — Final live run, audit and report
Status: TODO. Not started.

---

## WAITING_OWNER items (summary, kept in sync as they arise)
- Part 4: editorial_review_required news photos usable in published films? (default: not used)
- Part 5: approval of the Persian master before native rewrites
- Part 6: host time reading A vs B
- Part 7: look-image approvals before HeyGen upload; per-channel outfit allow-list
