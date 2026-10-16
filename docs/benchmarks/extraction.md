# Fact extraction cost reduction — Part 2(e) (Amendment 3)

No secrets, no raw third-party text — only numbers, ids and structure.

## Baseline (from the real ledger, case 5, no new spend)

Queried live against PostgreSQL, 2026-10-16:

| Metric | Value |
|---|---|
| Fact Extractor calls (all models) | 152 |
| tokens_in (sum) | 3,652,348 |
| tokens_out (sum) | 1,739,713 |
| cost_eur (sum) | EUR 10.8079 |
| mean tokens_in/call | 24,029 |
| mean tokens_out/call | 11,445 |
| mean EUR/call | EUR 0.0711 |
| sources in case | 88 |
| mean EUR/source | EUR 0.1228 |
| facts stored | 1,708 |

Of the 152 calls, 137 ran on claude-haiku-4-5 (both model-string variants — see Part 0b/ROADMAP) for EUR 10.166268; the remainder are prior-session experiments on other models (gemini-3.8-flash, gpt-5.6-luna), not part of this baseline.

**Acceptance target (per the spec): cut this to <= EUR 5.40 (50%) with equal-or-better fact recall.**

## "What share of facts survive" — could not be cheaply measured; documented as a real gap

The spec asks for the share of extracted facts later used by a contradiction, the timeline, the story plan or the final story. The current schema does not support this directly:
- `Contradiction` (app/db/models.py) stores `source_ids_json`, not `fact_ids` — there is no FK or id list from a contradiction back to the specific facts it was built from.
- `timeline_builder` (app/agents/research.py `_build_timeline`) refines `event_date` on **every** fact passed to it uniformly — it does not select a "timeline subset," so presence in its output says nothing about survival.
- The stored master story (case 5, version 1, `needs_revision`) is in **Persian** (`language='fa'`) while facts' `claim` text is English — a direct keyword-overlap check against the story text returned 0/80 on a random sample, which is a language-mismatch artifact, not a real 0% survival rate (confirms Part 3a's canonical-language concern independently).

Given this, fact recall for the acceptance bar is measured differently (and more reliably) below: whether a *known-relevant* fact (one whose source and content clearly bears on the case's contradictions/timeline, judged by a human/strong-model reading, not string matching) is still extracted after each change — a fixture-based check, not a live production-data survival percentage. A future improvement worth tracking: store `fact_ids_json` on `Contradiction` and on the story plan's evidence pack (Part 5a already requires "evidence ids per beat") — once that exists, true survival becomes a simple query.

## Reductions implemented (ordered, each measured separately)

### Step 1 — select before extracting (rank, dedupe, cap)

Implemented: `ResearchAgent._select_sources_for_extraction` (app/agents/research.py). Rank by `reliability_score` then content length; dedupe near-duplicates/syndicated copies via the existing embedder, same-language pairs only (`duplicate_source_min_similarity: 0.92`); cap to `max_sources_per_case_for_extraction` (config, default 50). Every skip recorded on `Source.extraction_skip_reason` (migration 0024).

**Real measurement on case 5** (dry run, 2026-10-16, no extraction spend — only the already-cached embedding lookup, case 5's `spent_eur` unchanged at 19.920886): of 88 real stored sources, **50 selected, 38 skipped** — all 38 skipped for "ranked below the per-case extraction limit" (none were duplicates at the 0.92 threshold, none had zero content). That is a **43% cut in sources entering extraction**, which should translate to a roughly proportional cut in Fact Extractor calls/EUR once combined with the remaining steps — on its own this one step does not yet reach the 50% target; re-measured against the real ledger once steps 2-6 land and a real extraction re-run is done.

### Step 3 — cache by content hash (done ahead of step 1/2, see Part 2 progress note in ROADMAP.md)

Implemented: `app/agents/research.py`'s `_extraction_cache_*` helpers + `ExtractionCache` (migration 0023). Content-hash + prompt-version + model keyed, portable across cases (source/chunk ids remapped), never applied to empty-content bundles. Reduces repeat-extraction cost to zero for unchanged content on a restart or a shared source; real EUR impact on case 5 not yet measured (would need a real re-run that repeats existing content, which hasn't happened yet).

### Step 6 — deterministic quote check (done ahead of step 1/2)

Implemented: `app/core/quote_check.py` + `Fact.quote_verified` (migration 0022). Does not yet reduce EUR directly (no LLM grounding step exists yet in this pipeline for it to let skip) — see ROADMAP.md Part 2 for detail.

### Steps 2, 4, 5 — not started

Chunk-level cutting by embedding similarity (2), compact output schema with span/offset evidence (4), and concurrent batching of small chunks (5) remain to be implemented before the full acceptance measurement (EUR cut >= 50% vs. baseline, fact recall equal-or-better, 0 schema failures) can be run for real on case 5.
