# Production dependencies, limits and caching

This describes what the code does today (checked 2026-11-18), not an ideal workflow. Tests that prove it: `tests/test_parallel_production.py`, `tests/test_visual_gate.py`,
`tests/test_elevenlabs_requests.py`, `tests/test_image_requests.py`, `tests/test_asr_workers.py`, `tests/test_orchestrator.py`, `tests/test_autopilot_runner.py`, `tests/test_language_pipeline_flow.py`.

## The rule

Research sources may be in any language. Research synthesis, fact verification and the master story target one English master story. No other-language narrative or voice-ready
text starts until the English final story has passed its quality gate (`LanguagePipeline.run_case`: the other languages run only from the approved English final). After that every
selected language writes its text on its own and in parallel. The gate is the English FINAL STORY. The English film is not a gate (`mvp.review.require_english_first` is a legacy
switch and is off).

## Dependency graph

`[H]` = mandatory human gate. `(a)` = automatic validation. Boxes in the same column run at the same time.

```
 research (any-language sources, once per case)                                   Autopilot phase "research"
        │
        ▼
 (a) evidence readiness ──► English master draft ─► (a) audit ─► (a) draft review ─► draft approved
        │                                                                                │
        ▼                                                                                ▼
 English final story  ◄──────── rewrite ─► inspect ─► final ─► (a) verify/fidelity/length gates ──┐
        │            ENGLISH FINAL-STORY GATE (the first and only text gate)                      │ a failed English final starts no other language
        ▼
 ┌──────────────── all selected languages write their own final text, in parallel (asyncio.gather) ───────────────┐
 │  de      fa      ar      es      it      fr      pt      tr      (en is already final)                         │
 └───────┬────────┬────────────────────────────────────────────────────────────────────────────────────────────────┘
         │ each language, the moment ITS OWN final text is approved (autopilot tick, about 20 s):
         ▼
 one film job per language (DocumentaryJob). Inside a job:
   ┌─ voice-ready text ─► performance ─► VOICE  [slot voice_stage 9] ─► [slot elevenlabs_tts 4 requests] ─► (a) listening check [asr 1] ─► mix [mix 1]
   │                                         │
   │                                         └─ fa only: [H] PERSIAN VOICE APPROVAL (voice.review_languages) ── holds Persian work after the voice, nothing else
   ├─ host text/media (parallel with the voice)
   └─ shared picture work: English job searches/verifies pictures; every other language uses the English plan's approved items (mvp/reference.py)
                    │
                    ▼
        [H] CASE-LEVEL PICTURE/VIDEO APPROVAL (mvp/visual_gate.py) ── one owner decision for the whole case, voided when the approved items change
                    │  (English job also stops once for the owner's picture review and cut review before this)
                    ▼
   direct (mvp_direct) ─► portraits ─► zoom maps ─► production script ─► critique ─► coverage ─► RENDER [slot render 1] ─► (a) review checks
                                                                                                       └─ fa only: [H] film render review (mvp.review.owner_review_languages)
         │  per language, as soon as ITS OWN inputs exist (no language waits for another language's film):
         │     titles ◄── the language's approved final text (+ the case)        hooks/metadata inside the titles and film description steps
         │     Shorts  ◄── the language's FINISHED film (its cuts, voice and timing)   → script + render per Short, cover review, spoiler protection
         │     dry-run publish queue ◄── film rendered + titles + Shorts settled (done/skipped)   (`sent: false` always)
         ▼
 Publishing Studio: plan (AI plan: only on the owner's Generate click) ─► [H] content approval ─► [H] publication approval (hash + expiry) ─► Emergency Stop check ─► publisher
                    automatic publishing: OFF (`automatic_publishing.enabled`); public posting: OFF (`facebook.allow_public_publish`); YouTube uploads are private until the owner says otherwise
```

### What runs together, what waits

| Runs concurrently | Waits for |
|---|---|
| Texts of all languages | The English final story |
| Voice of every language (up to 9) | Its own approved final text; only `fa` also waits for the owner's voice approval before the pictures and the film |
| The film jobs of all languages (cap `jobs` 9, admitted by the machine guard) | Their own text |
| Film direction/render of a language | Case-level picture approval; for `fa` also the voice approval |
| Titles of a language | Its approved final text (not its film) |
| Shorts of a language | Its finished film (the clips are cut from it; the script uses the words actually spoken there) |
| The dry-run publish queue of a language | Its rendered film, its titles, its settled Shorts |
| Real publishing | The Publishing Plan approvals in the Publishing Studio; nothing in the Autopilot publishes |

Story approval, the Persian voice approval, the picture approval and a film's approval are never permission to publish.

### Barriers removed, delays removed, what is left
- **Removed:** the Autopilot no longer stops after the films phase until every film is done. Titles, Shorts and the dry-run publish queue work on each language that is ready (`AutopilotRunner.advance`; the legacy English-only wave, `require_english_first`, still holds them back).
- **Removed:** the 20-second Autopilot tick (now `autopilot.tick_seconds` 3) and the fixed 20-second gap between film starts. The machine guard now holds back the expected memory of films that just started for `orchestrator.settle_seconds` (20 s) and admits the next film at once when memory, load and providers have room: nine films that fit start in one pass.
- **Not done: Shorts text and editorial planning before the film.** The Shorts pipeline is built on the finished film: moment selection runs per film job, the script writer is given the words actually spoken in the chosen stretch (`_cues_for` the rendered film), and the clips are cut from the render. Writing hooks and scripts earlier would change what the writer sees and the spoiler/fidelity checks that depend on it, so a Short is planned when its language's film is finished, but never waits for another language.
- **Left:** the Autopilot still re-reads the database every 3 s instead of reacting to events; film jobs finishing in the worker are noticed at the next tick.
- Rendering is one at a time (`render` 1): measured, see below.

## Limits audit

Classes: **provider quota** (stated by the provider), **measured** (measured on this Mac Studio), **guard** (correctness or data integrity), **old-Mac** (an assumption of the previous machine), **unverified** (a configuration choice nobody verified).

| Limit | Value | Class | Notes |
|---|---|---|---|
| `voice_stage` | 1 -> **9** | old-Mac | Serialized all narrations of all languages. Now one per language. |
| `jobs` (film jobs at once) | 5 -> **9** | old-Mac | Would have queued languages before their voice. The machine guard still admits jobs only while memory and load have room. |
| `elevenlabs_tts` | 4 | provider quota (from a code comment: the plan allows 5; not re-verified today) | Extra requests queue. A 429 halves the limit and it recovers. Kept at 4 until the account's allowance is verified. |
| `elevenlabs_sound` | 2 | unverified | |
| `asr` | 1 -> **4** | **measured** | Local faster-whisper on the CPU. One transcription uses 4 of the 20 cores. In one process with `num_workers` = threads: model `small` 9.1 -> 19.1 audio-s per s (4 workers, 2.1x, 2.4 GB), `large-v3-turbo` 6.0 -> 17.3 (2.9x, 3.4 GB), same text as a single run. With `num_workers` 1 four threads gain nothing (9.0), so the limit and `asr_check.workers` (4) move together. Separate processes: more than 4 runs get slower (8 runs: 14.5 vs 18.8). |
| `mix` | 1 -> **9** | **measured** | The app's own ffmpeg mix + two-pass loudness + mp3 on 30 minutes of narration: 78.8 s alone, 81.3 s with 3 at once, 84.0 s with 9 at once; 73 MB per process (`scripts/bench_mix.py`). Single-threaded streaming filters: one per language costs nothing. |
| `render` | 1 | **measured** | One libx264 encode already uses all 20 cores. 1 to 6 parallel encodes give 5.0 to 5.5 video-seconds per second. The real renderer's filters were not profiled (needs a real render). |
| `llm` 10, `vision` 4 | | unverified | Shrink on 429/503. No `Retry-After` header is read for these. |
| `downloads` 4 | | unverified | |
| `image` (covers, opening pictures, host looks) | none -> **2** | unverified (the gateway publishes no image quota; conservative) | One gate for every image request: extra requests queue; a 429 or an overloaded gateway halves the limit (it recovers) and its `Retry-After` is honoured before the next attempt (capped by `scene_image.max_retry_after_s`, 60 s); the budget is checked before every attempt; a request that was sent and not answered is marked as possibly billed in the attempt's recorded error and logged; the limit shows in the orchestrator monitor. |
| Governor: free memory 2 GB, 2.5 GB per film, load 1.5 per core, 20 s settle window | | guard; the per-film memory is an estimate | The settle window is not a wait: it only holds back the expected memory of a film that has just started. |
| Autopilot tick | 20 s -> **3 s** | recovery and polling interval | Cheap database reads only; newly ready work is noticed within 3 s. |
| Brave `search_min_interval_s` 1.0 | | unverified plan limit | Brave's `Retry-After` is read. |
| YouTube Data API quota | | provider quota | Counted by the app. |
| Meta Reels per 24 h (30) | | configured | |

## Caching and repeated paid calls

| What | Mechanism | Evidence |
|---|---|---|
| ElevenLabs speech | Per block, keyed by content (text, voice, settings): a block that exists is never requested again, also after a restart | `tests/test_voice_render.py` (edited block only) |
| Language pipeline stages | Each stage is its own row; a rerun resumes without calling a stage that has its row | `test_a_rerun_resumes_without_calling_a_stage_that_has_its_row` |
| Film jobs | A resumed job keeps its finished stages | `test_a_resumed_job_keeps_its_finished_stages...` |
| Autopilot | One job per language; a new runner (worker restart) makes no second job and launches nothing twice | `test_a_restarted_runner_makes_no_second_job...` |
| HeyGen | The job id is stored with the voice checksum; a scene with a job is not submitted again | `host_scenes.py` |
| Opening music | Library of tracks; the cost is booked once per track | `music_cost.py` |
| Web pages, searches | 7-day fetch cache, search ledger | `config/connections.json`, `visuals/search_log.py` |
| LLM prompt caching | `cache_control` for claude-sonnet-5-5, `prompt_cache_key` for gpt-6.1-sol, none for gpt-5.6-luna (not assumed) | `config/models.json` |

### Uncertain outcomes (no exactly-once claim)
- **ElevenLabs:** the provider documents no idempotency key that we use. A request that was sent and got no answer (timeout, read error) may have been billed. It is retried, and
  the block's saved metadata now records `uncertain_prior_attempts` and a warning is logged, so the possible double charge is visible (`tests/test_elevenlabs_requests.py`). A clear refusal (429) is not counted.
- **Worker restart during a request:** a request in flight when the worker stops is lost, and the restarted job asks again. The cache protects every block that finished.
- **LLM:** failed attempts are booked with their tokens as discarded; a call in flight at a crash is not recorded.
- **Image generation:** same rule as speech: a request that got no answer may have been billed; it is marked and logged, and the next attempt is a new request (no idempotency key is documented).
- **Not done:** reading `Retry-After` for LLM providers, and a per-provider daily budget table.
- **Worker restart (verified on PostgreSQL with scratch jobs):** a job whose worker died is requeued up to `job_max_auto_resumes` times, then left `interrupted` for a person; its finished stages stay `done` and only the running one is redone; completed and cancelled jobs are untouched.
