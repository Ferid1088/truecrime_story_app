# Every cost and volume limit, by stage — all of them live in `config/`

The only hard EUR limit is the per-case cap; the rest bound how much a stage searches, checks, retries or stores.
`tests/test_limits_in_config.py` fails if one of the limits below comes back as a constant in code.

| stage | limit | where |
|---|---|---|
| whole case | actual spend cap EUR 300 (a case may carry its own) | `policies.json` `budget.total_cap_eur` (+ `Case.total_cost_cap_eur`) |
| discovery | cost estimate averaged over the last N runs: 20 | `limits.json` `discovery.cost_estimate_sample_size` |
| research (web) | queries, rounds, fetches, results per language; time; Brave key | `connections.json` `search_engine.*` (6 queries, 3 rounds, 10 fetches, 10 results, 900 s) |
| research (YouTube) | queries and results per language, results per query | `research.json` `youtube_research.*` (8, 8, 20) |
| research job | wait for it: poll 15 s, at most 6 h | `limits.json` `research_job.*` |
| story / language pipeline | rewrite and audit retries; inspector attempts 2 | `documentary.json` `max_redos`, `limits.json` `language_pipeline.inspector_attempts` |
| visual planning | queries per entity 4 exact + 3 context, 5 aliases | `limits.json` `planner.*` |
| visual director | beats per model call: 10; candidates per requirement 12 | `limits.json` `director.beats_per_call`, `visuals.json` `visual_direction.candidates_per_requirement` |
| picture search | queries 150, candidates per query 15, assets 1500, download 15 MB, case storage 3000 MB | `visuals.json` `visual_search.*`, `storage_budget.*` |
| picture supply | rounds 8, empty rounds 2, **research cost cap EUR 30 per run** | `visuals.json` `supply.*` |
| production search | new pictures checked per request 3, entity match 0.7, requests 40 | `limits.json` `production_search.*`, `visuals.json` `visual_direction.max_search_requests` |
| vision checks | thresholds, second pass | `visuals.json` `visual_verification.*` |
| audio | music-library misses kept 8 | `limits.json` `audio.max_miss_tags` |
| footage | queries 25, clips per case 40, pieces per source 30, download 250 MB | `visuals.json` `footage.*` |
| YouTube clips (switch off) | source 1800 s, download 400 MB, clip 16 s, 8 % of a film | `video_rights.json` `youtube_clips.*` |
| shorts | USD 2.00 per case (Settings may override) | `documentary.json` `short_form.production.max_cost_usd_per_case` |
| autopilot | attempts per paid phase: 3 | `autopilot.json` `max_attempts_per_phase` |

Removed on 2026-10-21 because nothing read them and they only looked like limits: the five research profiles
(`max_cost_usd`, call counts, rounds, novelty) and `research.max_*_per_language`/`minimum_result_novelty`, and
`budget.stage_estimate_eur`.
