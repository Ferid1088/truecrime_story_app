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
- **Clean narration**: with `audio_direction.beds_under_narration=false`
  (default) no music plays under the narrator's words. The Music/Audio
  Director puts music only into the gaps — between sections, before a
  revelation, after a strong statement, at chapter turns, under silent
  picture sequences — and may choose SILENCE (room tone) instead, above
  all around disturbing facts, reveals and open questions. Moods follow
  the emotional function of the moment (suspense, investigation,
  mystery, melancholy, danger, discovery, tension, relief, resolution,
  uncertainty); every cue and silence carries a `why`.
- **Music library** (`music_tracks`): several generated variants per
  kind and mood (ElevenLabs sound generation, normalized, cached in
  `data/music_library/`). A film keeps one theme per mood across all its
  languages; a track used by one of the last
  `music_library.reuse_after_videos` (10) other films is not chosen while
  an alternative exists or can be generated. `music_usages` records
  every placement with the director's reason and the selection reason.

### Visuals, production and render (the documentary video)

Policy (V1): real photos, documents, maps, typography and black frames —
no AI video. "If the listener closes their eyes, the documentary must
still work. If they open them, the visuals deepen understanding."

1. **Visual needs** (`visual_planner`): per beat what the viewer should
   see (people, places, objects, documents) with search queries; plus a
   map place, an anchor date, a short real quotation or a document
   passage — each checked against the evidence (exact quotes only).
2. **Visual research → media library**: images from the case's own
   sources (news articles: og:image, figures with captions), Wikimedia
   Commons (license metadata; polite User-Agent and request spacing) and
   the SearXNG image search; **footage** from Wikimedia Commons video and
   the Internet Archive (licensed only), stored as short MUTED clips with
   a keyframe for verification. Every asset keeps source, rights,
   license, entity, the query it was found for, the date found and a
   **relevance tier**: 1 exact case evidence/footage, 2 the exact
   person/place/object, 3 the exact city/building/area, 4 contextual
   licensed imagery, 5 generic atmosphere (last resort). Downloads are
   size-checked, deduplicated by perceptual hash and stored per case
   (`data/cases/<id>/visuals/`).
3. **Rights** (deterministic): owned, licensed, public_domain,
   creative_commons, editorial_review_required, permission_required,
   unknown, do_not_use (stock libraries, watermarks). FOUND is not
   USABLE: `rights.allowed_for_render` decides per render profile
   (`preview` = internal review copy, `publish` = cleared material only).
4. **Verification** (`visual_verifier`, vision model): what the image
   actually shows vs. what it is claimed to show; role (evidence /
   context / illustration); branding or burned-in text; sensitive
   content. Rejected images are never used; humans can override in the
   Visual Library.
5. **Visual direction** (`visual_director`) answers for every sentence
   "what should the viewer see while this is spoken?" — lowest tier
   first, the person when the sentence is about them, real footage
   (SHOW_CLIP, muted) where it helps, and REQUEST_SEARCH when nothing
   fits. The **visual_gaps** stage then searches for weak sentences
   (e.g. "St Mary's Church <town> exterior"), verifies what it finds and
   re-directs those beats; the audit sits in the plan's
   `search_requests`. **Repetition control** (`media_usages`): generic
   and contextual pictures and maps appear at most once or twice per
   film, central people may return (with room in between) when the
   sentence names them; every reuse records why. **Maps** appear where
   geography matters — at the first mention of a place, never as the
   film's first picture unless the opening is `important_location`,
   once per place — zooming country → region → city → the relevant
   area. Openings follow the film's opening strategy. Commands:
   KEEP_CURRENT_IMAGE, NEW_IMAGE, SHOW_CLIP, SHOW_MAP, SHOW_DOCUMENT,
   SHOW_DATE, SHOW_QUOTE, BLACK_SCREEN, REQUEST_SEARCH, …. Validator:
   reveal firewall (no picture shows what a later reveal/evidence beat
   discloses), cognitive load (one thing to read, none during dense
   narration), minimum holds, illustrations labelled on screen, fallback
   hierarchy (unused verified picture → document/date card → hold →
   black). Motion is deterministic and
   subtle (slow push/pull, pans, focus on faces, document highlight, map
   zoom, light 2.5D parallax), never the same move three times in a row.
6. **Production script** per language from the real audio (cuts every
   ~5–9 s, sentence-snapped; `case_status`, `production_type`,
   `opening_strategy`; an UNSOLVED case gets an "UNSOLVED CASE" status
   card at the start and near the end): shots,
   transitions, overlays localized per language (dates formatted
   deterministically, place names/quotes by `overlay_localizer`),
   music placements, silences, subtitles, credits.
7. **Critics** (`automation_feel_critic`, `attention_critic`,
   `visual_accuracy_critic`, `production_critic` — never the director's
   model) plus deterministic checks; targeted fixes per shot
   (replace picture, keep previous, change motion, black, remove text).
   Each language is judged on its own. A cross-film variety check flags
   a film that repeats the previous films' opening strategy, first shot,
   music tracks and cut rhythm (`template_repeat`).
8. **Render** (FFmpeg + OpenCV + Pillow, no editorial logic): 1080p/25
   MP4, H.264 + AAC, soft subtitle track and `.srt`, credits for
   attributed material.

**One button**: the Documentary section of the app (`/documentary`)
runs `POST /api/cases/{id}/documentary/jobs` — (from zero: research →
master story →) blueprint → audio plan → storyteller text per language →
film-length check → visual needs → research → verification → visual plan
→ voice performance → voice → production script → critique → render. Stages are resumable; a re-run reuses everything that
exists (`refresh_visuals` re-plans pictures). `mode: "pilot"` renders the
opening `pilot_seconds` of every language; `mode: "full"` renders the
whole film and refuses stories outside 45–120 minutes
(`documentary.min_film_minutes` / `max_film_minutes`; story length in
the Studio is 45–120 minutes too).

Setup on top of the voice requirements: `pip install -r
requirements-documentary.txt` (Pillow with raqm for Persian/Arabic
shaping, OpenCV). Fonts (SIL OFL) ship in `app/documentary/assets/fonts`.
Maps use OpenStreetMap tiles and Nominatim by default (credited on
screen, cached in `data/map_cache`); switch `maps.tile_url` /
`maps.geocoder_url` to a commercial provider for heavy or published
use. All four narrators use ElevenLabs `eleven_v3` with `language_code`
(v3 has no request stitching) and Persian uses a larger Whisper model for
the speech check (`asr_check.languages.fa`).

### Case lifecycle: selection, status, monitor, follow-ups

Brief: `docs/Master_Task_Case_Lifecycle.MD`. Pipeline: discovery →
duplicate checker → status verifier → research → media research →
story director → visual director → music/audio director → production →
render → video record + YouTube metadata → archive → unsolved monitor →
follow-up.

- **Which cases are suggested**: RECENT + SOLVED + NEVER USED. Discovery
  searches recent developments (verdicts, convictions, charges;
  `case_selection.seed_queries`, SearXNG `time_range`), the extraction
  reports status and dates, the best candidates' status is verified with
  targeted searches and `case_status_verifier`, and the ranking is
  recency (half-life `recency_half_life_days`) × status weight. UNSOLVED
  cases are only suggested with `include_unsolved`. Every suggestion
  stores its reason; duplicates and filtered ones are stored with theirs.
- **Duplicate checker** (`app/lifecycle/identity.py`): compares identity,
  not titles — victim/suspect names (spelling-tolerant: Gehricke ≈
  Gericke, Müller = Mueller), aliases, places, dates, case-specific
  source URLs and identifiers — against every case in any state and every
  suggestion ever shown. Manual case creation and "investigate" answer
  409 with the matched case and the reason (`force` overrides).
- **Resolution status** `SOLVED | UNSOLVED | UNKNOWN |
  STATUS_UNDER_REVIEW` is a case field with a history
  (`case_status_history`: who, why, sources). Visible on discovery
  cards, case lists (filter), case page, documentary jobs, production
  scripts, videos, YouTube titles and the archive
  (`GET /api/archive?status=SOLVED|UNSOLVED|ALL`).
- **Unsolved monitor** (in-app scheduler, about twice a week:
  `case_monitor.interval_hours`; `POST /api/monitor/run` by hand; set
  `TRUECRIME_DISABLE_SCHEDULER=1` to keep it off): stage 1 runs a couple
  of searches per UNSOLVED case limited to the time since its last check
  and looks for deterministic signal words in results that name the case
  — no signal: stop (no fetch, no LLM). Stage 2 only after a signal:
  pages are read, `case_status_verifier` judges, and SOLVED needs
  confidence AND two independent sources or one official source; weaker
  evidence → STATUS_UNDER_REVIEW. Every check is stored
  (`case_status_checks`).
- **Follow-ups**: a covered case (it has a video) that goes UNSOLVED →
  SOLVED appears on the dashboard: "PREVIOUSLY COVERED UNSOLVED CASE —
  NOW SOLVED … Do you want to create an update video?". Nothing is
  produced before approval (`POST /api/follow-ups/{id}/approve`), which
  starts one `follow_up` job: update research, a master that opens with
  the earlier episode ("We first told this story in Episode 27 …"), and a
  video linked to the original.
- **Videos** (`videos`): one per rendered language with status at
  production/publication, opening strategy and YouTube metadata —
  "UNSOLVED: <title>", follow-ups "SOLVED: The <case> Case — What
  Happened After Our Original Video" (localized). Publish sets the
  episode number and freezes the status at publication.
- **Openings vary**: the story director chooses an opening strategy
  (critical moment, mysterious statement, victim introduction, evidence
  discovery, emergency call, important location, contradiction, last
  sighting, courtroom outcome, unanswered question, timeline anomaly)
  and avoids those of the most recent films (`opening.avoid_recent`).
- **Audit**: `GET /api/cases/{id}/audit` — why suggested, duplicates,
  status changes and checks, and per production why each picture, map,
  repeat, music cue or silence was chosen.

### Pronunciation check (Persian)

Persian script leaves short vowels unwritten: «ملک» is melk (property),
molk (realm), malek (king) or malak (angel); «جنت» is jannat, not
jennat; «اندام» is andam, not endam. The voice guesses — in a live test
with 12 such sentences it guessed wrong 6–7 times. Whisper cannot catch
it: it writes Persian without short vowels too (malk and molk both come
back as «ملک»). So every voice block goes through a listening loop:

1. **Pronunciation key** (`pronunciation_editor`, in the voice
   performance stage): for each word a reader could misread, the reading
   the MEANING needs (checked against the English source), the minimal
   harakat that force it (`مُلک`), full harakat (`مُلْک`), optionally an
   unambiguous spelling and a synonym with the same meaning. A key whose
   reading and harakat contradict each other is not used.
2. **Listening**: after every take a phoneme recognizer
   (`facebook/wav2vec2-xlsr-53-espeak-cv-ft`, IPA) hears the block; each
   key word is found at its exact place in the audio (ElevenLabs
   character timing, then its own consonants) and its vowels are compared
   with the key (a wrong short vowel — malk for molk, jennat for jannat —
   is an error; ā heard as o, e as i are tolerated). Whisper still checks
   that no word was skipped or changed.
3. **Correction loop**: a wrong word gets harakat in the text the voice
   reads → spoken again → listened to again; then full harakat; then the
   unambiguous spelling; last a synonym (only then do the subtitles
   change). Up to `pronunciation.max_rounds`; anything still wrong is
   flagged `pronunciation_unresolved` in the manifest.

Live result (12 sentences, key written by the model): 27 words checked,
6 said wrong on the first take, all 6 fixed (5 by harakat, 1 needed full
harakat); in another run «گل» (mud) stayed "gol" with harakat and was
fixed with the synonym «لجن». Subtitles and the Whisper check always use
the text without added harakat. Setup: `pip install -r
requirements-documentary.txt` (CPU torch + transformers; the model,
~1.2 GB, downloads on first use). Without them the loop is skipped and
the manifest says so (`pronunciation_error`).

### Voice performance (ElevenLabs v3 audio tags)

`POST /api/documentary/versions/{version_id}/voice-performance`
(`{"beat_ids": [...]}` for a pilot; `GET` returns the latest) — the voice
performance director (`voice_performance_director`) turns the spoken text
into the narrator's performance:

- **Arc** per beat (one call over the whole film): level 0 *neutral*
  (nothing has happened yet), 1 *unease*, 2 *dark* (the crime is present,
  lower and more deliberate), 3 *breath-taking* (slow, measured, close to
  a whisper). Rules: everything before the first incident and the
  opening beat stay neutral, tension rises in steps, recovery beats calm
  down, at most one beat in five reaches a climax.
- **Sentences** (chunks in parallel): a level per sentence and the text
  for the voice — v3 audio tags from the palette of that level
  (`voice_performance.level_tags`, e.g. `[pause]`, `[thoughtful]`,
  `[softly]`, `[slowly]`, `[sighs]`, `[tense]`, `[whispers]`,
  `[whispering, slowly]`) placed right before the 4–5 words they colour,
  ellipses and dashes for timing, and (English only) one emphasised word
  in capitals.
- **Validator**: forbidden tags removed (laughter, crying, shouting,
  sound effects, accents, excited/playful — this is a real crime), at most
  `max_tags_per_sentence`, density per level (`max_tagged_share`: a
  narrator who performs every line sounds fake), climaxes at most
  `max_climax_share` of all sentences, no trailing tags, sentence type
  kept, and the words themselves unchanged — otherwise the plain sentence
  is used.
- The directed performance script sends the tagged text to the voice,
  picks the block's voice style from its level
  (`voice_performance.level_styles`: speed 0.94 → 0.84), lengthens the
  breath after tense paragraphs, and keeps the plain text for subtitles
  and checks. Audio tags are never counted as words or speech time.
- `GET /api/documentary/versions/{version_id}/speech` shows, sentence by
  sentence, what the narrator reads and what people read.

### Parallel work

Two levels (limits in `config/ai_config.json` → `concurrency`):

- **Inside one documentary**: the spoken versions of all languages and
  the visual needs run together; then the visual chain (research →
  verification → shot direction) runs next to each language's chain
  (voice performance → voice), and every language continues to
  production → critique → render as soon as the pictures are planned.
  Image checks, critics, director chunks, spoken repairs and voice
  blocks run in parallel too. A language that fails does not stop the others
  (job status `partial`, resumable).
- **Several documentaries at once**: `POST /api/documentary/batch`
  (`{"items": [{"case_id": 3}, {"case_id": 7, "from_zero": true,
  "target_minutes": 60}], "languages": [...], "mode": "pilot"}`) starts one
  job per case; `concurrency.jobs` run at the same time, the rest wait in
  `queued`. `from_zero` jobs first research the case with the search
  engine and write the master story. `GET /api/documentary/scheduler`
  shows what runs and how full every limit is.
- Shared limits pace everything in the process: `llm`, `vision`,
  `elevenlabs_tts` (the ElevenLabs plan allows 5 parallel requests; a
  "too many concurrent requests" answer waits and retries),
  `elevenlabs_sound`, `asr`, `render`. More API keys or a bigger plan →
  raise the matching limit. SQLite runs in WAL mode so the UI keeps
  reading while jobs write.

## Workflow پیشنهادی

1. `POST /api/topics/discover`
2. یکی از موضوع‌ها را انتخاب کنید.
3. `POST /api/cases`
4. منابع را با `POST /api/cases/{case_id}/sources` اضافه کنید.
5. `POST /api/cases/{case_id}/research`
6. `POST /api/cases/{case_id}/generate-story`
7. `GET /api/cases/{case_id}/story/latest`

خروجی آخر فقط متن داستان است.
