# Implementation plan for the film planning redesign (DRAFT, 2026-10-30)

Base: `docs/specs/film_planning_redesign_2026-10-30.md` (the owner's spec, sections 1-16). This plan only says **how and in what
order**; the spec says what. Nothing here is built yet. Migration head today: 0079 (`migrations/versions/`).

## Ground rules (from CLAUDE.md, apply to every phase)

- Commit locally after each phase; **no push** without the owner's request. Run `scripts/secret_scan.py` before each commit.
- Every bug fix gets a test; every new config value goes into `config/`; every new column/table gets an Alembic migration that is
  run on **live PostgreSQL** and shown with a query. No dead code and no commented-out code: **old code is removed in the same phase
  that replaces it.**
- "Verified" means a test (`file::name`) **and** live evidence on case 15 (real stack, real providers). Paid proof runs stop before a
  paid call if `spent_eur >= cap_eur` (case 15 was reset to 0 of 300 on 2026-10-29). English only; Persian and the others reuse the English cards.
- The frontend is Next.js with breaking changes: read `frontend/node_modules/next/dist/docs/` before writing Next-specific code.

## What the code looks like today (measured)

- The nine removed card kinds appear in about **50 files**: 15 test files, 7 config files, 6+4+2 prompt files, `app/documentary/graphics`
  (+ `engines/`), `app/documentary/detective`, `app/documentary/visuals`, `app/documentary/mvp`, the frontend e2e tests, and 40 golden images.
- Strategy agents: `detective/strategy.py`, 2 prompts, table `detective_strategies` (model `DetectiveStrategy`, migration 0079),
  references in `toolkit.py`, `ai_config.py`, `director.py`.
- General open search: `mvp/general.py`, its stage in `mvp/pipeline.py`, agent entries in `config/agents.json` and `config/models.json`,
  `tests/test_mvp_general.py`, `ai_config.py`.
- YouTube already exists: `visuals/youtube_clips.py`, `youtube_quota.py`, `footage.py`, `research.py`, `official_sources.py`.
- Review API: `mvp/api.py` (reviews, candidates, decisions, gaps, neighbour-cover); UI: `frontend/components/documentary/review-queue.tsx`
  and `tabs/planner.tsx`.
- Logo: `render/logo.py` + `config/parameters/branding.json`; renderer `render/engine.py`; card shots placed in `production/script.py`.

## Phases (each leaves the app working, tested and committed)

### Phase 0 - baseline (small)
- Commit the two spec files. Run the full test suite and record the result as the baseline.
- Record the numbers of the current English film (job 56) to compare with at the end: cards share, bare seconds, black seconds, repeats.

### Phase 1 - fix what is broken now (small-medium)
- **Map missing from the film:** find why the planned map shot disappears in `visuals/generated.py` / `production/script.py`
  (failed place lookup turned into "KEEP_CURRENT_IMAGE"); fix, with a test; live proof: a map shot in the case 15 script.
- **Duplicate cards:** about 10 identical pairs came from repeated Director passes. Add a content fingerprint check at card creation
  (this becomes the planner's memory in phase 3); test with two cards of the same claims.
- Remove the `detective_board`/map special cases I added in `dispatch.py` only if phase 3 makes them unnecessary (decide there).

### Phase 2 - remove old code (large, mechanical)
1. **Nine card kinds** (`key_facts`, `unknowns`, `conflict_board`, `investigation_clock`, `custody`, `interrogation`, `courtroom`,
   `sentencing`, `floor_plan`): delete their graphics (`keyfacts.py`, `floorplan.py`, the engines they use), registry entries, dispatch
   builders, `buildable.NEEDS`, `tool_catalog`, themes, labels and engine config in `graphics.json`, prompts, tests, golden images
   and frontend e2e references. Keep `story.py`/`trial.py`/`evidence.py` only for what stays (`evidence_check`, `forensic`).
2. **Strategist agents:** delete `strategy.py`, `film_strategist_system.md`, `card_strategist_system.md`, the `detective_strategist`
   role and agent entries, config (`strategy_enabled`, `required_kinds`, `min_card_kinds`, diversity passes), and **drop table
   `detective_strategies`** (migration 0080, run on live PostgreSQL, show `\d`/information_schema afterwards).
3. **General open search:** delete `mvp/general.py`, its pipeline stage, `mvp.general_check` agent and model entries, prompt, test.
4. Case 15 stored plans that use removed kinds: reset the toolkit plan for case 15 (the existing reset keeps pictures and approvals).
   Other cases are not touched (destructive tests only on scratch cases, never case 5).
- Check: full test suite green, `grep` finds no reference to the removed kinds/agents, the app starts and a job reaches the planner.

### Phase 3 - the new toolkit (large, the core)
- **Design note first** (half a page, in the spec): what stays of the reveal graph, claims and scenes tables (they can carry the cards and
  their metadata: which part of the text a card belongs to) and whether the planner's memory needs its own table or the content
  fingerprints on the cards are enough.
- **Text analyser agent:** reads the narration, estimates time from the text, splits it into parts.
- **Card agents:** one per remaining kind (`fact_card` at most 10, `recap`, `conflict`, `timeline`, `evidence_check`, `forensic`, `quote`,
  `headline`, `document`), invoked per part by the analyser; the **board agent is the existing one** (continues its previous work);
  the map is the planner's, from a stored place.
- **Metadata** on every card: covered text part (unit keys), kind, claims used, estimated time.
- **Planner memory:** a card whose content (claims/text) was already used is refused; no card repeats.
- **Card inspector agent** (new, `detective.card_inspector`; the existing cutter, inspector and usable agents are untouched): checks each
  card for logical problems, returns them to the toolkit, the toolkit fixes, the card is inspected again; a configured number of
  rounds, then the card is dropped.
- **Share:** cards + board + map target **30-40 %**; two attempts to reach at least 30 %; a shortfall is accepted; never above 40 %.
- **Run speed:** keep the lessons of job 54 (low reasoning for many small calls, chunking that shrinks after a split, deterministic
  builders where the data suffices).
- Check: tests per agent with fakes; live: a toolkit run on case 15 with the call count, tokens, minutes, cost, share and zero repeats.

### Phase 4 - timeline card (medium)
- Same-date events merged under one time point; each next time point strictly later; invalid order is rejected by the validator.
- **Snake layout** in `graphics/timeline.py` (rows that turn at the page edge, fits the page as it grows, mirrored for fa/ar).
- Check: unit tests for merge/order; render tests (a long timeline in en and fa); live: the timeline card of case 15 looked at.

### Phase 5 - card timing and look (medium)
- Cards shown **15-20 s** (the toolkit's hard limit); the planner may extend by up to **5 s** (maximum 25 s); a card whose text cannot be read
  in the maximum is split or refused.
- **Reading time computed dynamically** by the planner (a normal reader, config in `config/`), text streaming **2x faster** than today
  (`graphics.json` stream settings).
- **Logo corner kept free** in every card layout: move the frame decorations (`graphics/frames.py`) away from the top corner at the end of the
  reading direction.
- Check: golden thumbnails regenerated, a test that the logo corner is empty, a test of the 15-20 s limits.

### Phase 6 - the planner and the logo (medium)
- The Main Planner receives cards + metadata and arranges them with the pictures and videos by the text; the **map and the board are in every
  film, and a timeline**; it extends cards within the 5 s.
- **Logo always on screen** for the whole film, added by the planner as an overlay (`render/logo.py`, `branding.json`: all shot kinds, no windowing
  gaps), none drawn on cards.
- Check: a render test that the logo is present in every frame range; live: frames sampled across the case 15 film.

### Phase 7 - research and the two owner reviews (large)
- **Caps:** 5 per identity from the references; from YouTube, per identity, **20 photos (frames) and 5 clips**, a separate budget (owner, section 18 of the spec). No batch downloads.
- **YouTube frames and clips** (extend `youtube_clips.py`/`footage.py`): frame extraction of good scenes, an agent that picks a span and the app
  cuts it as a clip; existing audits, logo/watermark cut-away and the second check stay.
- **Stages:** planner plans and researches -> inspection and approval/rejection (nothing new planned) -> **owner review 1** -> planner trims and cuts
  -> inspector checks again -> **owner review 2 (only trimmed/cut items)** -> planner plans again (gaps under 4 s by neighbours, longer gaps by the
  drawing) -> render. No new search after review 2.
- **Owner decisions are frozen:** a service-level guard so only the owner (and, after trimming, the inspector for an item it cannot repair) can change
  an approval or rejection; a test that every other code path fails.
- **New columns** on the asset or its candidate (migration 0081, live): `must_use` (owner's checkbox) and `draw_priority` (1-10, photos only).
  The must-use item is used 100 % (a video: its whole approved cut, once).
- **Replan on owner change:** timing only, no new toolkit run.
- Check: tests for caps, for the guard, for each stage order; live: review 1 and 2 on case 15 with real items.

### Phase 8 - review UI (medium-large)
- Read the Next.js docs in `frontend/node_modules/next/dist/docs/` first. Extend `review-queue.tsx` / `tabs/planner.tsx`: the must-use checkbox,
  the draw-priority field (photos only), review 2 showing **only trimmed and cut items**, and the pause/resume of the job.
- Check: frontend e2e tests; live: operate it in the browser on case 15 (screenshot evidence).

### Phase 9 - the drawing (large)
- New module (for example `app/documentary/drawing/`): the stroke-based portrait renderer from the demo (`pencil_demo5.py`) as a proper class:
  stroke plan from the photo, **progress video** of the bare canvas (pencil position per frame), rendered once per chosen photo, in parallel.
- **Compositor** in the render engine: window flush in the corner (top-left en/de, top-right fa/ar), 120x150 on 1280x720, **60 % visible** over pictures,
  **100 % full screen** in gaps of 4 s or more, paused and hidden over cards, speed within 0.5-2x chosen by the planner; the "illustration" label
  shown beside it.
- Photo choice from the owner's priorities (1..N); the number of portraits is not fixed: it follows photo complexity, speed (0.5-2x) and film length.
  Only photos whose licence allows a derived work; no spoilers (starts when its subject is introduced), no injury or crime-scene photo.
- **All languages:** the progress video is rendered once per photo and reused; only corner, label side, card pauses, gaps and speed are set per language.
- Check: unit tests (stroke plan, progress mapping, pause over cards, corner by language); live: a case 15 render with a drawing, frames inspected.

### Phase 10 - render pipeline in parallel (medium-large)
- The film render starts when the timing without avatar and cards is known; cards, host scenes, maps and the drawing progress video are made in
  parallel and composited at the end. Optional, after measuring: render per segment (chapter) and join.
- Check: end-to-end timing compared with the baseline of phase 0.

### Phase 11 - live proof and clean-up (medium)
- Full English film on case 15 through the whole new process; report against the spec: card share 30-40 % (or the honest shortfall), no repeats,
  15-20 s cards, map, board and timeline present, logo throughout, bare/black seconds, review 1 and 2 with real clicks, cost, minutes.
- Final grep for removed code, docs and CHANGELOG, full test suite.

## Order and dependencies

`0 -> 1 -> 2` first (smaller surface). `3` needs `2`. `4` and `5` can follow `3` in parallel. `6` needs `3`-`5`. `7` is independent of `3`-`6`
and can start after `2`; `8` needs `7`. `9` needs `7` (priority column) and `5`/`6`; `10` needs `9`. `11` last.

## Risks and how the plan handles them

- **30-40 % cards may not be reachable** with no repeats and only the remaining kinds: phase 3 reports the real number early (before the planner,
  review UI and drawing depend on it).
- **Large deletions:** phase 2 is mechanical but touches about 50 files; tests and `grep` are the net, and it is done before new code.
- **YouTube downloads** depend on quota and on the platform; the existing quota code and caps stay; failures degrade to fewer candidates, not a crash.
- **Rights:** other channels' footage and photos of real people stay preview-only under the existing rights status; publishing is the owner's decision.
- **Live data:** migrations 0080 and 0081 are run on live PostgreSQL with a query; case 15's toolkit plan is reset once (backup exists:
  `data/backups/truecrime_before_toolkit_fill_20261029_0159.dump`).
- **Time and cost:** the toolkit run was about 43 minutes and 6 euros in the last measured run; the drawing progress render is about 11 minutes
  per portrait (parallel); case 15 has 300 euros available.

## Decisions made (2026-10-30)

1. YouTube budget: 20 photos and 5 clips per identity.
2. Label beside the drawing window: yes.
3. No fixed portrait count or total drawing time; it follows photo complexity, drawing speed and film length. The drawings serve all languages.
