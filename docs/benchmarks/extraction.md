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

See ROADMAP.md Part 2 for status. This file is updated as each of the 6 ordered steps lands.
