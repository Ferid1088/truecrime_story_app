# ROADMAP

State file for the rebuild described in `docs/changing.txt`. One row per part. Status: TODO / IN_PROGRESS / DONE / WAITING_OWNER / BLOCKED.
Inside each part: an inventory note, a requirement table (test + live evidence per requirement), the three review passes, and "not verified" items.

## Amendment 1 (received 2026-10-15, mid-Part-0)

The owner sent an amendment to the master prompt. Recorded verbatim in effect, applied as follows:

- **Patch F** replaces Part 5 items (b) and (c) — see the Part 5 section below for the full patched text. Part 5 had not been started when this arrived, so Patch F is used in place of the original (b)/(c) when Part 5 is reached; nothing needed to be redone.
- **Part 10 — One real 60-minute film from zero** is inserted as a new part, run AFTER Part 8 and BEFORE the (unchanged) Part 9, which stays the final audit/report and runs last. See the Part 10 section below.
- The COMMON HEADER and LOOP PROTOCOL apply unchanged to both.

## Parts

| ID | Title | Status | Commit | Tests | Live proof | Not verified | Next owner action |
|----|-------|--------|--------|-------|------------|---------------|--------------------|
| 0 | Foundation | WAITING_OWNER | 939d594 | test_generation.py + test_cost_ledger.py (new/changed), full suite 1266 passed/10 skipped, 3-pass review done (all findings fixed) | See Part 0 section below | Part 0b case-5 recompute not yet written to the DB | Approve `python -m scripts.recompute_case_costs 5 --apply` (or decline/ask for something different) — case 5's real spend is EUR 19.92, not the EUR 15.78 the ledger currently shows |
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
| 0b Cost ledger prices by resolved model, books failed-call tokens, fails loudly on missing price | tests/test_generation.py (9 new `_chat`/`generate_text`/`generate_structured` tests), tests/test_cost_ledger.py (8 new tests) | See "Part 0b live proof" below | DONE (code + 3-pass review fixes); recompute dry-run only, awaiting owner confirmation to write |
| 0c No cost-estimate gates block a run; actual-spend cap only | tests/test_budget.py (all), tests/test_pilot_master_regressions.py::test_record_provider_call_never_raises_a_stage_cost_ceiling, ::test_a_20s_pilot_stage_is_never_blocked_by_cost_estimate | Ran `pytest tests/test_budget.py tests/test_pilot_master_regressions.py`: 23 passed. No code change needed — already correct. | DONE (pre-existing) |
| 0d SearXNG/search fallback removed (Brave only) | — (no behavior to test; dead config/docs only) | `grep -rni searxng` across app/, config/, scripts/, tests/, README.md, .gitignore → zero hits after cleanup (only ROADMAP.md/docs/changing.txt mention the word, as history) | DONE |
| 0e /health commit_mismatch correct | tests/test_api.py::test_health_flags_a_mismatch_even_when_worker_liveness_probe_is_negative | Restarted API+worker on commit a67502c; `GET /health` → commit=a67502c99c8b6326817ea127439a47c74185a7b5, worker_commit=same, worker_alive=true, commit_mismatch=false | DONE |

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
- `scripts/recompute_case_costs.py` (new, dry-run by default, `--apply` to write) recomputes exactly the 223 mispriced rows from their own stored tokens: **spent_eur before: EUR 15.777983 → after: EUR 19.920885** (dry run, independently reproduced bit-for-bit by the pass-2 reviewer agent; not yet written — see OWNER ACTION below). Case 5's real spend was ~26% higher than the ledger showed, 8 cents from the EUR 20 cap.
- OWNER ACTION (does not block the loop): writing this correction to the live case 5 rows needs the owner's go-ahead (the Claude Code auto-mode classifier itself declined to write to the shared production database without explicit permission) — the owner compares the new total with the gateway's own usage page, then either approves `python -m scripts.recompute_case_costs 5 --apply` or asks for something different.
- `/health` re-verified after a clean restart on commit 939d594: `commit=939d59434c84d48931b96c222e70a98d7077907e`, `worker_commit`=same, `worker_alive=true`, `commit_mismatch=false`.

### Not verified
Everything except 0a, 0c, 0d, 0e, and 0b's code fix (including its 3 review-pass fixes). The case-5 recompute is dry-run only until the owner approves writing it.

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
OWNER GATE (pending): confirm host time — Reading A (120s/appearance, 180s/film total, default built) vs Reading B (120s total for the whole film, per-appearance cap dropped).

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
- Part 0b: approve writing the case-5 cost-ledger recompute (`python -m scripts.recompute_case_costs 5 --apply`) — see Part 0 section above
- Part 4: editorial_review_required news photos usable in published films? (default: not used)
- Part 5: approval of the Persian master before native rewrites; (Patch F) owner's old-vs-new writer judgement overrides the metrics
- Part 6: host time reading A vs B
- Part 7: look-image approvals before HeyGen upload; per-channel outfit allow-list
- Part 10: case choice for the 60-minute film (not case 5 unless chosen); approval of its Persian master mid-run
