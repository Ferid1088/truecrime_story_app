# TrueCrime Story Studio

یک اپلیکیشن پایتونی برای:

- پیدا کردن پرونده‌های جدید که قبلاً روی آن‌ها کار نشده
- ذخیره‌ی تاریخچه‌ی پرونده‌ها در دیتابیس
- جمع‌آوری و ثبت منابع جدا از متن داستان
- استخراج فکت‌ها و تناقض‌ها
- ساخت «پرونده‌ی تحقیق»
- طراحی زاویه‌ی روایی
- نوشتن داستان جدید و جذاب
- بازبینی جذابیت و شباهت
- خروجی نهایی: فقط متن داستان
- نگهداری منابع، فکت‌ها، تناقض‌ها و نسخه‌ها به‌صورت جداگانه در دیتابیس

## معماری

```text
OpenRouter Research Provider (web search + fetch, single gateway)
        ↓
Topic Discovery Agent / Deep Research
        ↓
Case Registry / Database
        ↓
Sources → Facts → Contradictions
        ↓
Story Director
        ↓
Writer Agent
        ↓
Engagement Critic
        ↓
Similarity Critic
        ↓
Final Story Text
```

## External research provider (OpenRouter)

OpenRouter is the single gateway for all web research — search, source
fetch, multilingual queries and YouTube discovery run through its
server tools (`openrouter:web_search`, `openrouter:web_fetch`). Devin
was removed from active workflows; historical ResearchJob rows with
provider="devin" remain readable data.

```text
app/providers/base.py    ResearchProvider interface + ProviderJob
app/providers/research_openrouter.py   OpenRouterResearchProvider
  (iterative loop: query plan → web_search → evaluate → web_fetch →
   refine → stop on low marginal value)
app/services/research_jobs.py   persistent ResearchJob lifecycle + ingestion
```

- `TrueCrime_OPENROUTER_API_KEY` in `.env` (backend-only; never sent to
  the frontend, logged, or stored in DB rows) — the only active AI secret.
- `GET /api/integrations/research/status` → `{provider, configured, reachable}`.
- `POST /api/topics/discover` and `POST /api/cases/{id}/research` return
  `{job_id, status}`; poll `GET /api/research-jobs/{job_id}` (or
  `GET /api/research-jobs?case_id=`).
- On completion, sources are stored as `Source` rows (provider metadata:
  `research_provider`, `external_reference`, `published_at`, `retrieved_at`,
  `summary`, `status`). `possible_facts`/`possible_contradictions` stay in the
  job result — unverified claims never enter the Fact layer directly; the
  Research Agent's extraction stage remains the fact path.
- If the key is absent the app falls back to the legacy LLM discovery /
  research path; provider failures return
  `503 "Research provider unavailable"` and leave stored data intact.

## نکته مهم

این پروژه عمداً «دانلود و بازنشر ویدئوی دیگران» را انجام نمی‌دهد.
برای یوتیوب، متادیتا و لینک‌ها را می‌توان جمع کرد، اما متن/ترنسکریپت باید از منبعی
استفاده شود که شما حق استفاده از آن را دارید یا به‌صورت مجاز در اختیار اپ قرار گرفته باشد.

## اجرا

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
uvicorn app.main:app --reload
```

سپس:

- API docs: http://127.0.0.1:8000/docs

## Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

The UI talks to the FastAPI backend at `http://127.0.0.1:8000` by default.
Override with `NEXT_PUBLIC_API_URL` in `frontend/.env.local` if needed.

Backend tests: `pytest tests/`

## Generation provider (OpenRouter)

OpenRouter is the generation/reasoning provider. Only the secret lives in
`.env`; every model and generation parameter lives in
`config/ai_config.json` — swapping a model means editing that one file.

```env
TrueCrime_OPENROUTER_API_KEY=...
```

`config/ai_config.json` defines three model aliases:

- `cheap` — `openai/gpt-5.6-luna`
  (structured extraction: facts, timeline, contradictions)
- `writer` — `google/gemini-3.8-flash`
  (story direction, long-form writing, rewriting)
- `premium` — `anthropic/claude-sonnet-5.5`
  (engagement critique, final editorial review)

Role routing (`fact_extractor`, `timeline_builder`, `contradiction_analyzer`,
`story_director`, `writer`, `rewriter`, `engagement_critic`, `final_editor`)
maps each agent role to an alias. If a routed model fails with a
provider-side error (rate limit, unavailable, timeout) the provider walks the
configured `fallbacks` chain for that alias and records `fallback_used` on the
AgentRun; the model that actually produced each output is recorded on the
AgentRun (provider/model/role/temperature) and StoryVersion
(`generation_provider`, `generation_model`).

- `GET /api/integrations/openrouter/status` → `{provider, configured,
  reachable, models, routing, fallbacks}`.

Agents never contain model IDs, temperatures, token limits or thresholds —
they ask `ai_config.model_for(role)` / `generation_for(role)`.

## Documentary foundations

Groundwork for the audio-first documentary engine (`app/documentary/`).

- **Act structure survives every step.** Stories are written per act with
  `[[ACT:id]]` markers; repairs, the final editor, `improve` and
  localization now keep those markers, and each StoryVersion stores its
  act text in `narrative_structure.sections`. Localization falls back to
  act-by-act transcreation if a one-call rewrite drops markers. A final
  polish that would destroy the structure is rejected.
  `structure_lost_at` records any step that still lost it.
- **Evidence meaning is tracked.** Evidence IDs (F001…) follow database
  order, and every version stores an `evidence_fingerprint`. Localizing a
  master written from an older evidence set is refused
  (`localization.require_current_evidence`).
- **Pilot length.** `story.min_target_minutes` / `max_target_minutes`
  (default 3–90) replace the hard-coded 10-minute floor.
- **Reviewers stay independent.** `review_independence` lists author and
  reviewer roles; a reviewer never routes or falls back to a model that
  writes narration.
- **Voice blocks.** `GET /api/cases/{case_id}/stories/{version_id}/voice-blocks`
  plans text-to-speech blocks (config `voice_blocks`, default 30–90 s):
  never cuts a sentence or a quotation, keeps a speaker's "…said:" with
  its quote, starts a new block at every act, prefers paragraph ends, and
  flags (never cuts) a sentence longer than the maximum. Sentence
  detection is pysbd plus repair rules for German/Persian/Arabic.

### Voice (narration audio)

Setup: `TrueCrime_ELEVENLABS_API_KEY` in `.env` (the key needs the
text-to-speech permission), `ffmpeg` installed, and
`pip install -r requirements-documentary.txt` for the speech-to-text
check. Voice ids, model ids, styles, pauses and loudness live in
`config/ai_config.json` (`voice`, `asr_check`, `loudness`).

- `POST /api/cases/{case_id}/stories/{version_id}/voice/render`
  `{"max_seconds": 180}` renders the opening (whole blocks) — omit it
  for the full story. CLI: `python -m scripts.render_voice --case 2
  --version 9 --max-seconds 180`.
- Per block: ElevenLabs with character timestamps (neighbouring
  sentences sent as context), trim to the reported speech, word
  timestamps, independent local Whisper check (skipped / extra / changed
  words; a failing block gets one automatic re-take with another seed),
  loudness per block. Blocks are joined with app-level pauses and the
  narration is normalized to −16 LUFS with one constant gain.
- Unchanged blocks come from cache (`data/cases/…/blocks`), so re-runs
  only pay for edited blocks; `force_block_ids` records a new take.
- `GET …/voice` → manifest (QA per block + word timeline),
  `GET …/voice/narration.mp3` → audio.

### Editorial blueprint and performance

- `POST /api/cases/{case_id}/stories/{version_id}/blueprint` — the
  Narrative Director (role `narrative_director`) divides the finished
  story into beats (paragraph ranges per act) with purpose, first-time
  reveals (evidence ids), listener questions (opened / answered /
  honestly *unresolved*), human focus, low/medium/high load levels and
  attention, visual, audio, pause and music intents. A deterministic
  validator checks coverage, ids, reveal order, open-question load, beat
  length and pacing; one repair round fixes errors, one improvement
  round (kept only if better) addresses listener-load warnings.
  Status: `valid` | `needs_review` | `invalid`. `GET …/blueprint` returns
  the latest.
- `GET …/performance` — voice blocks with style (from
  `performance.style_for_intent`), beat ranges and the pause after each
  block. Style changes and dramatic pauses/silences are block
  boundaries; the silence director keeps long pauses rare (share cap,
  never two in a row unless both are reveals/chapter ends) and slightly
  varied in length. `voice/render` uses this automatically when a usable
  blueprint exists for exactly that story text; the manifest then has a
  beat timeline (`timeline.beats`).

### Spoken storytelling (EN / DE / FA / AR)

A script that reads well on paper sounds like a news bulletin when it is
spoken. `POST /api/cases/{case_id}/stories/{version_id}/spoken?language=en`
(needs a valid blueprint for that story) retells it, beat by beat, the
way a storyteller talks to one listener:

- **Writer** (`spoken_writer`, Claude): rebuilds every beat in its own
  spoken sentences instead of polishing the old ones: one idea per
  sentence, at most `spoken.max_sentence_words` words, spoken connectors,
  paragraphs of two to four sentences (each paragraph break is a breath
  in the recording). Each language has a native house style and
  before/after examples (German Präteritum and Konjunktiv for claims,
  Persian spoken standard without officialese, Arabic storytelling
  fusha without «تمّ»/«من قِبَل» calques). Facts, names, numbers,
  quotations and certainty never change. Comments a model adds for the
  "user" are removed, and the call is retried once.
- **Checks by other models** (group `spoken_adaptation` in
  `review_independence`, strict): a meaning check per beat (missing,
  added or changed facts, certainty) and a native style critic
  (storyteller | mixed | newsreader, with the exact phrases and spoken
  alternatives), plus a deterministic ear check (sentence length, stiff
  written phrases). Up to `spoken.max_repair_iterations` repair rounds.
- **Gates**: meaning unchanged, no newsreader beat, at least
  `spoken.min_storyteller_share` storyteller beats, few long sentences,
  language quality, clean output, similar speaking time.
- The result is a StoryVersion (`kind: "spoken"`) whose sections are the
  blueprint's beats, so pauses, music and later visuals line up beat by
  beat in every language.

`review_independence.groups` gives each pipeline its own author/reviewer
set: a reviewer never routes or falls back to a model that writes the
text it judges, and in a `strict` group the writer never falls back to
its critics' model either.

### Audio direction and music

- `POST …/audio-plan` — the audio director (`audio_director`) decides
  per beat: breath between paragraphs, a quiet music bed (mood, very
  low/low), and what follows the beat: breath, music bridge (scene
  change), emotional moment, sting (turn/reveal), near-silence or
  chapter break. A validator clamps lengths (`audio_direction.
  transitions`), keeps music moments special (spacing, share cap) and
  ends the film on the last beat. One plan per blueprint serves all four
  languages; `GET …/audio-plan` works for spoken versions too.
- With an audio plan, `GET …/performance` is a *directed* script: a
  breath between paragraph groups, the planned transition after every
  beat. `voice/render` then mixes `documentary.wav/.mp3`: beds under
  runs of beats (EQ'd to leave room for the voice), bridges and
  emotional moments in the planned gaps (starting softly under the last
  words), stings, room tone for silences, final loudness −16 LUFS.
  `with_music: false` renders the narration only.
- Music cues (`music_library`) are generated once with ElevenLabs sound
  generation, normalized and cached in `data/music_library/` — shared by
  every film and language.

## Workflow پیشنهادی

1. `POST /api/topics/discover`
2. یکی از موضوع‌ها را انتخاب کنید.
3. `POST /api/cases`
4. منابع را با `POST /api/cases/{case_id}/sources` اضافه کنید.
5. `POST /api/cases/{case_id}/research`
6. `POST /api/cases/{case_id}/generate-story`
7. `GET /api/cases/{case_id}/story/latest`

خروجی آخر فقط متن داستان است.
