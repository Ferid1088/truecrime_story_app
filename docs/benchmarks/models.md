# Model benchmark — replacing Haiku for the "cheap" and "research_intelligence" roles

Part 1 of `docs/changing.txt` / `ROADMAP.md`. No secrets, no raw third-party text — only model ids and numbers.

## Why

`config/models.json` routes 15 roles to `"cheap"` and 3 roles to `"research_intelligence"`, both of which resolve to `claude-haiku-4-5`. The project rule is "no Haiku anywhere" (CLAUDE.md). This document records, per job type, which model replaces it and why — a cheaper-or-equal-cost model that is not worse than Haiku on quality, measured against real stored data, not assumed.

## Real model catalog (apimaster.ai, live `GET /v1/models`, 2026-10-15)

79 models total. Excluding image/video-generation-only models (seedance, midjourney, kling, muse-spark, doubao-seedream, gpt-image-\*, gemini-\*-image, grok-imagine-\*) and embedding/transcription utility models (bge-m3, text-embedding-3-small, gpt-transcribe), the text/JSON-capable candidates are:

| Family | Model ids |
|---|---|
| Claude | claude-haiku-4-5, claude-sonnet-5, claude-sonnet-5-5, claude-sonnet-4-6, claude-opus-4-7, claude-opus-4-8, claude-opus-5, claude-opus-5-5, claude-fable-5, claude-fable-5-1 |
| Gemini | gemini-3.5-flash, gemini-3.6-flash, gemini-3.7-flash, gemini-3.8-flash, gemini-4-argon, gemini-3.1-pro-preview |
| GPT | gpt-5.6-luna, gpt-5.6-terra, gpt-5.6-sol, gpt-6-sol, gpt-6-astra, gpt-6-luna, gpt-6.1-sol |
| Qwen | qwen3.7-plus, qwen3.7-max, qwen3.8-flash, qwen3.8-max |
| DeepSeek | deepseek-flash, deepseek-v4-flash, deepseek-v4-pro, deepseek-v3.2, deepseek-v4-flash-vision-exp |
| GLM | glm-5.2, glm-5.3, glm-5.3-flash, glm-5.3-flashx |
| Kimi | kimi-k2.6, kimi-k2.7-code, kimi-k2.8-preview, kimi-k3 |
| MiMo | mimo-v2.5, mimo-v2.5-pro, mimo-v2.6-flash, mimo-v2.6-pro |
| MiniMax | MiniMax-M3, MiniMax-H3 |
| Grok (text) | grok-4.5, grok-4.6, grok-4.7 |
| Other | jev-latest, codex-auto-review (purpose unclear from the catalog alone — not shortlisted) |

**Pricing**: apimaster has no `/pricing` or `/models/pricing` API endpoint (probed live: 404 on both). Its `/docs/models` page lists prices for only 5 models (claude-haiku-4-5 $1/$5 per 1M input/output tokens — matches `config/parameters/pricing.json` exactly; claude-sonnet-4-6 $3/$15; claude-opus-4-7 $5/$25; gpt-5.4 $2.5/$15; gpt-5.5 $5/$30 — the last two aren't even in the live catalog, so this page is a stale/partial reference, not authoritative for the catalog above). Per the project's Honesty rule, every candidate's real price below comes from its own benchmark calls' `usage.cost`/token-rate fields (the same mechanism the production cost ledger already uses), never assumed from documentation.

## Shortlist (3-4 candidates + Haiku baseline)

Reasoning: vendor diversity (never all from one lab), "flash"/cheap-tier naming suggesting a lower price point than Haiku's, and live-confirmed `openai` endpoint-type support (so each candidate is callable through the existing `OpenAICompatibleGenerationProvider` unchanged — no new provider code needed for the benchmark itself).

| Candidate | Why shortlisted |
|---|---|
| `claude-haiku-4-5` | Baseline (current production model) |
| `gemini-3.8-flash` | Already in production as the "writer" role — a known-good quantity, good to check against cheap-tier jobs too |
| `qwen3.8-flash` | Flash-tier, confirmed `openai`+`anthropic`+`openai-response` endpoint support (strongest structured-output signal among non-Claude candidates) |
| `deepseek-v4-flash` | Flash-tier, vendor known for cost efficiency |
| `glm-5.3-flash` | Flash-tier, different vendor (Zhipu) for diversity |

Gold labels: `claude-sonnet-5-5` (current "premium" role), spot-checked by the implementer per the spec.

## Per-job-type schema inventory

Architecture note (applies to all 18 roles below): `run_agent()` (`app/agents/runner.py`) resolves `config/agents.json`'s per-agent `.prompt`/`.output`, then calls `generate_text`/`generate_structured`. There is **no schema-enforced/tool-calling validation anywhere today** — `generate_structured` just parses JSON into a plain `dict`; no Pydantic models exist for any agent output (only two bare `@dataclass`es, `SemanticClassification` and `PlannedQuery`, do any field-level validation at all). The authoritative JSON shape for most roles lives in an `output_schema` dict the Python caller injects into the prompt's user message, not in the prompt file itself (see Part 2, which adds real pydantic-validated schemas for every stage — out of scope here).

**`discovery`** is routed in `config/models.json` (`"discovery": "cheap"`) but has no `config/agents.json` entry and no code path calls it — dead/legacy routing. The real case-discovery work is `case_discovery_agent` (routed to `research_intelligence`). Flagged for removal alongside the Haiku cleanup, not benchmarked.

**`semantic_consistency_checker`** covers two distinct agents/schemas (`story.compression_semantics` and `localization.semantic_check`) that both route to "cheap" — benchmarked separately below.

| Role | Agent key / prompt | Output (primary field) | Caller |
|---|---|---|---|
| fact_extractor | `research.extract_facts` / `agents/research/extract_facts.md` | `facts[]` (claim, category, confidence, source_ids, disputed, event_date, evidence_strength, supporting_text, chunk_ids, ...) | `app/agents/research.py` |
| timeline_builder | `research.build_timeline` | `timeline[]` (id, event_date) | `app/agents/research.py` |
| contradiction_analyzer | `research.analyze_contradictions` | `contradictions[]` (topic, description, source_ids, severity) | `app/agents/research.py` |
| discovery | *(dead config, no code path — see note above)* | — | — |
| grounding_validator | `story.grounding` | `supported_claims[]`, `unsupported_claims[]`, `uncertainty_errors[]`, `grounding_score` | `app/agents/story.py` |
| consistency_checker | `story.consistency` | `violations[]` (type, severity, detail, location) | `app/agents/story.py` |
| semantic_consistency_checker (a) | `story.compression_semantics` | `direct_assertion`, `attribution_present`, `confidence`, `rationale` (-> `SemanticClassification` dataclass) | `app/longform/compression.py` |
| semantic_consistency_checker (b) | `localization.semantic_check` | `semantic_consistency_score`, `missing_information`, `added_information`, `meaning_changes`, `uncertainty_changes` | `app/agents/localization.py` |
| localized_grounding_validator | `localization.grounding` | `supported_claims[]`, `unsupported_claims[]`, `uncertainty_errors[]`, `grounding_score` | `app/agents/localization.py` |
| evidence_normalizer | `evidence.normalize_sources` | `summaries[]` (id, summary_en) | `app/services/research_jobs.py` |
| transcript_normalizer | `transcripts.normalize` | `translations[]` (id, text_en) | `app/services/transcripts.py` |
| transcript_intelligence_extractor | `transcripts.extract_claims` | `claims[]`, `narrative_insights[]` | `app/agents/transcript_intel.py` |
| claim_clusterer | `claims.merge` | `groups[]` (canonical_claim_en, claim_ids) | `app/services/claims.py` |
| evidence_verifier | `claims.verify` | `verdicts[]` (cluster_id, verification_status, confidence, supporting_fact_ids) | `app/services/claims.py` |
| research_evaluator | `research_engine.analyze_gaps` | `remaining_gaps[]`, `resolved_gaps[]`, `new_contradictions[]`, `novel_information_detected` | `app/research_engine/planner.py` |
| research_assembler | `research_engine.summarize` | `summary`, `coverage`, `thin_areas[]` | `app/research_engine/planner.py` |
| research_query_planner | `research_engine.plan_queries` | `queries[]` (query, language, purpose, priority, expected_source_types) -> `PlannedQuery` dataclass | `app/research_engine/planner.py` |
| result_reranker | `research_engine.rerank` | `evaluations[]` (idx, relevance, credibility, novelty, fetch_recommendation) | `app/research_engine/planner.py` |
| case_discovery_agent | `research_engine.discover_cases` | `candidates[]` (title, resolution_status, narrative_potential, source_richness, ...) | `app/research_engine/orchestrator.py` |

**Benchmark execution order** (data-driven: case 5's real ledger shows call volume per stage, highest first — `Fact Extractor` 110 calls, `research` [query planner + reranker + evaluator] 73, `Transcript Intelligence` 24, `Grounding Validator` 9, `Claim Clusterer` 4, `Timeline Builder`/`Contradiction Analyzer` 3 each, others 1 each): fact_extractor benchmarked first (this document's Results section), remaining roles follow the same harness pattern (`scripts/benchmark_fact_extractor.py` as the template — generalizing it to the other 17 roles is tracked in ROADMAP.md Part 1).

## Benchmark methodology

Per job type: up to ~40 items from stored real data (including Persian items where the job type operates on Persian text), scored on:
- **Valid-JSON rate**: did `generate_structured` return parseable JSON within the existing one-retry repair budget?
- **Agreement with gold**: job-type-specific comparison against the sonnet-5-5 label (see the schema table for what "agreement" means per job type — e.g. fact-set overlap for extraction jobs, label match for classification jobs).
- **Unsupported claims**: does the output assert something not backed by the input text (checked by the existing grounding-validator mechanism where applicable, or a dedicated check per job type).
- **Latency**: wall time per call.
- **Actual cost per call**: read from the real `provider_calls` ledger row this benchmark run produces (never estimated).

## Results

(to be filled in as the benchmark runs — see ROADMAP.md Part 1 for current status)
