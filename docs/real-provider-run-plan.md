# First real-provider run plan

Status: plan only. This document does not authorize or perform a provider run.
The first run must use a disposable case and the isolated stack until the
operator explicitly approves a real-provider execution.

## Credentials and provider keys

Never put secret values in the repository, a prompt, a Playwright trace, or a
policy event. The dry run checks that the required variable names exist and
that their values are not printed.

| Capability | Provider/config | Required key or setting | Current state |
| --- | --- | --- | --- |
| LLM generation, critics, naming, Shorts writer/auditor | APIMaster | `TRUECRIME_APIMASTER_API_KEY` | required |
| Web search and fetch | local TrueCrime/SearXNG | `TRUECRIME_SEARXNG_URL` | required; no external key in this app |
| YouTube discovery/transcripts | YouTube adapter | `TRUECRIME_YOUTUBE_API_KEY` | required only if the selected discovery path uses YouTube API |
| Narration/TTS | ElevenLabs | `TRUECRIME_ELEVENLABS_API_KEY` | required for narrated output |
| Host/avatar video | HeyGen | `TRUECRIME_HEYGEN_API_KEY`, `TRUECRIME_AVATAR_ID_HEYGEN` | disabled by default; do not enable in the first run |
| Database and isolation | PostgreSQL | `TRUECRIME_DATABASE_URL` | must resolve to the approved disposable database |

The first run must also set `TC_TEST_NO_NETWORK=0`, an explicit API base, and a
case-level total cap of EUR 20 (the configured default). It must fail before any provider request if the database
is the live database, a required key is missing, the selected model is not
configured, or the cap is absent.

## Models and routing

These are aliases from `config/models.json`; the run records the resolved model
name with every provider call.

| Alias | Resolved model | Intended stages |
| --- | --- | --- |
| `cheap` | `gpt-5.6-luna` | extraction, fact/timeline/contradiction checks, grounding and consistency |
| `research_intelligence` | `gpt-5.6-luna` | research query planning, reranking, case discovery |
| `writer` | `gemini-3.8-flash` | story direction/writing, localization, visual planning, Shorts audit |
| `premium` | `claude-sonnet-5-5` | final editors, engagement critics, native-language critics, Shorts writing, status verification |
| `embedding` | `bge-m3` | embeddings only; not a text-generation call |
| TTS | ElevenLabs `eleven_v3` | en/de/fa/ar narration, using the configured per-language voice IDs |

HeyGen is out of scope for the first run. FFmpeg and ffprobe are local tools,
not provider spend, and must remain the render authority.

## Estimated cost envelope

These are preflight ceilings and planning estimates, not invoices. Actual
vendor prices and token usage must be recorded from the provider response and
compared with the cap before the next stage starts.

All stages share one configurable per-case cap expressed in EUR. Provider USD
prices are converted with the configured `usd_to_eur` rate before the ledger is
checked. The default cap is EUR 20; the existing Shorts cap remains an
additional Shorts-specific guard.

| Stage | Calls/units to budget | Planning envelope per case |
| --- | --- | ---: |
| Research | search/fetch plus configured LLM query planning, reranking and evaluation | `€0.50` estimate |
| Naming | writer candidates plus title critics | included in the cap |
| Master story | director, writer, grounding/consistency/engagement/final-edit passes and capped repairs | `€1.00` estimate |
| Localization | one writer plus quality/grounding/engagement/final-edit passes per language | `€0.50` per language stage |
| Thumbnail approval | critic/vision calls and any thumbnail redo | `€0.20` estimate |
| Shorts writing/audit | two calls per short, plus at most two redos; 4 selected shorts × enabled languages | `€2.00` estimate plus Shorts cap |
| Narration | ElevenLabs characters/minutes per language | `€2.00` estimate |
| Local render/package | FFmpeg, captions, cover and manifest | `$0` provider spend; local CPU/disk budget only |

The hard stop is the total case cap, checked before every paid stage. The case
is persisted as `budget stopped` with a retry/raise-cap action when the next
estimate would exceed the remaining amount. The configured per-case Shorts cap
(`max_cost_usd_per_case` currently `$2.00`, overridable by the case setting)
remains an additional guard. Provider spend is recorded
per Shorts queue item. When the cap would be exceeded, the Shorts item is
skipped with a persisted reason and the long film remains completed. The first
real run should use a lower operator-selected cap until the payload and billing
ledger have been reviewed.

## Dry-run checklist

1. Confirm the operator has explicitly approved the case ID, languages, model
   aliases, maximum spend, and whether TTS is included. This document alone is
   not approval.
2. Resolve the database URL and prove it is disposable; record the database
   name and Alembic revision without changing the live database.
3. Verify the isolated API/frontend pair and worker health. Do not run
   `scripts/dev_up.sh` and do not touch ports 8001/3001.
4. Verify key presence by variable name only. Never echo values. Verify the
   APIMaster, ElevenLabs, SearXNG and optional YouTube endpoints with cheap
   health checks, not a generation request.
5. Resolve every requested agent role to a configured model and record the
   routing snapshot. Refuse unknown roles, fallback loops, or a missing price
   table.
6. Create a fresh case fixture with a fixed language list, expected source
   count, output directory, and content-hash baseline. Ensure retries are
   idempotent.
7. Run a no-send dry run that predicts provider calls, tokens/characters where
   available, estimated cost, queue items, and the maximum possible redo cost.
   Refuse the run if the prediction exceeds the case cap.
8. Run one small recorded/sandbox request first and inspect parsing, language,
   empty-answer, over-length, audit, and persisted-cost behavior.
9. If approved, run research and naming first; stop and review their persisted
   outputs before starting master story or TTS.
10. Run the master story and each localization behind their job state UI. A
    timeout or provider error must fail the job visibly and leave the case
    retryable.
11. Approve the thumbnail and language bundles by content hash. Re-read hashes
    immediately before any upload operation; a mismatch must fail closed.
12. Render Shorts with the production worker. Verify every requested language,
    dimensions, caption-burn flag, subtitle direction, output hash, and queue
    cost before any release action.
13. Keep default visibility `private`/`draft`. Do not use `release now` during
   the first run. Upload only through the YouTube adapter contract: connect
   OAuth separately for each language channel, then use resumable private/draft
   upload, scheduling, quota retry and token-reconnect handling. The recorded
   adapter fake covers quota exceeded, token expiry, partial upload and success;
   no live call is part of this plan.
14. Save the final evidence: commit, database revision, job IDs, provider-call
    ledger, per-stage cost, cap decisions, hashes, output file checks, and all
   skipped/not-run reasons. Do not label the run done if any evidence is
   missing.

## Recorded dry-run result

The no-send endpoint runs the same stage planner for one tiny English-only
case, builds a prompt record for every planned stage, de-duplicates the shared
paid-stage estimates, and returns the real configured EUR cap and estimate.
It sends no provider request and writes no upload. With the default stage
estimates, the English first-run estimate is EUR 6.00, below the EUR 20 cap;
the returned result includes `sent: false` and `within_cap: true`.
