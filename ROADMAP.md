# ROADMAP

State file for the rebuild described in `docs/changing.txt`. One row per part. Status: TODO / IN_PROGRESS / DONE / WAITING_OWNER / BLOCKED.
Inside each part: an inventory note, a requirement table (test + live evidence per requirement), the three review passes, and "not verified" items.

## Parts

| ID | Title | Status | Commit | Tests | Live proof | Not verified | Next owner action |
|----|-------|--------|--------|-------|------------|---------------|--------------------|
| 0 | Foundation | IN_PROGRESS | | | | | |
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

Status: IN_PROGRESS

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
| 0b Cost ledger prices by resolved model, books failed-call tokens, fails loudly on missing price | | | TODO |
| 0c No cost-estimate gates block a run; actual-spend cap only | tests/test_budget.py (all), tests/test_pilot_master_regressions.py::test_record_provider_call_never_raises_a_stage_cost_ceiling, ::test_a_20s_pilot_stage_is_never_blocked_by_cost_estimate | Ran `pytest tests/test_budget.py tests/test_pilot_master_regressions.py`: 23 passed. No code change needed — already correct. | DONE (pre-existing) |
| 0d SearXNG/search fallback removed (Brave only) | — (no behavior to test; dead config/docs only) | `grep -rni searxng` across app/, config/, scripts/, tests/, README.md, .gitignore → zero hits after cleanup (only ROADMAP.md/docs/changing.txt mention the word, as history) | DONE |
| 0e /health commit_mismatch correct | tests/test_api.py::test_health_flags_a_mismatch_even_when_worker_liveness_probe_is_negative | Restarted API+worker on commit a67502c; `GET /health` → commit=a67502c99c8b6326817ea127439a47c74185a7b5, worker_commit=same, worker_alive=true, commit_mismatch=false | DONE |

### Review passes
Not yet run (part in progress).

### Not verified
Everything except 0a.

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
