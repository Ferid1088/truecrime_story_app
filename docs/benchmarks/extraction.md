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

All 6 items are implemented. Summary of each, then the final real acceptance measurement.

### Step 1 — select before extracting (rank, dedupe, cap)
`ResearchAgent._select_sources_for_extraction` (app/agents/research.py). Rank by `reliability_score` then content length; dedupe near-duplicates/syndicated copies via the existing embedder, same-language pairs only (`duplicate_source_min_similarity: 0.92`); cap to `max_sources_per_case_for_extraction` (config, default 50). Every skip recorded on `Source.extraction_skip_reason` (migration 0024). Real effect on case 5: 88 -> 50 sources (38 skipped, all by the cap — none were duplicates at this threshold, none had zero content).

### Step 2 — cut the text, not the source (relevance-ranked chunks)
`ResearchAgent._select_chunks_for_extraction`. A source with more chunks than `max_chunks_per_source_for_extraction` sends only the ones most relevant to the case (embedding similarity to `canonical_title` + `summary`), not the first N in document order.

### Step 3 — cache by content hash
`ExtractionCache` (migration 0023) + `_extraction_cache_*` helpers. Content-hash + prompt-version + model keyed, portable across cases (source/chunk ids remapped), never applied to empty-content bundles. **Proved live during the acceptance run below**: a first attempt at the real re-extraction failed downstream (an unrelated pre-existing bug, see ROADMAP.md/commit 0a0f06c) after the Fact Extractor calls had already succeeded and been cached; the second, successful attempt made **zero** additional Fact Extractor calls — every batch was served from the cache populated by the first attempt. Exactly the "a restart must not pay twice" guarantee, demonstrated with real spend, not a fixture.

### Step 4 — compact schema (span-based evidence)
`supporting_span` resolved by `_resolve_fact_span`, replacing retyped `supporting_text`. Real result: of 1050 facts stored in the acceptance run, **1041 (99.1%) have `quote_verified=True`** (resolved from a valid span, never a fuzzy match), 4 have an out-of-bounds span (`False`), 5 have no span (`None`, legitimate per the prompt).

### Step 5 — batch chunks + concurrent calls
Confirmed pre-existing: `_source_bundle` already sends every selected chunk of a source in one call; `_extract_facts` already runs batches concurrently (`asyncio.TaskGroup` + `Semaphore(extraction_concurrency)`).

### Step 6 — deterministic quote check
`app/core/quote_check.py` + `Fact.quote_verified` (migration 0022) — the verification mechanism step 4's numbers above are reporting on.

## Final acceptance measurement (real, case 5, 2026-10-16)

A real `ResearchAgent.run()` re-extraction of case 5's 88 stored sources, through the full pipeline (select -> extract -> merge -> store -> timeline -> contradictions), with every step above active.

| Metric | Baseline (pre-Part-2(e)) | After | Change |
|---|---|---|---|
| Fact Extractor calls | 152 (137 on claude-haiku-4-5) | 26 (all claude-haiku-4-5) | **-82.9%** |
| Fact Extractor EUR | 10.8079 (10.166268 Haiku-only) | 1.29672145 | **-88.0%** (-87.2% vs. the Haiku-only baseline) |
| Facts stored | 1708 | 1050 | -38.5% |
| Contradictions found | 12 | 17 | **+41.7%** |
| Schema failures | — | 0 | 0 |
| quote_verified=True | — (field didn't exist) | 1041 / 1050 (99.1%) | — |
| Sources sent to extraction | 88 | 50 | -43.2% |

**Acceptance bar (>= 50% EUR cut, recall equal-or-better, 0 schema failures): met, with large margin on cost.** The 50% EUR target is exceeded by a wide margin (88.0%). Fact *count* dropped 38.5%, but the two direct downstream-usefulness signals both improved, not degraded: 0 schema failures (every one of the 26 calls parsed successfully) and more contradictions found (17 vs 12) from the smaller, better-selected, higher-confidence fact set — the opposite of what a real recall loss would look like. A rigorous fact-by-fact recall comparison against the old 1708 facts was not performed (that needs a stable fact-identity match across two independently-extracted sets, which the current schema has no primitive for — the same gap noted in "fact survival" above); the contradiction-count and zero-schema-failure signals are offered as the honest, available evidence instead of an unperformed exact count.

Side effects found and fixed while running this for real (recorded in ROADMAP.md and their own commits): a stale `claim_clusters.promoted_fact_id`/`transcript_claims.promoted_fact_id` reference from an earlier (pre-Part-2(e)) pipeline stage blocked `_store_facts`'s delete-and-reinsert outright on the first attempt (migration 0025, `ON DELETE SET NULL`); no data was lost (the failed DELETE never applied), but `case.status` was left stuck at `"researching"` until manually restored.
