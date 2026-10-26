# Alternative graphics — plan (source: `docs/alternavie_options.pdf` + owner prompt of 2026-10-26)

Branch `feature/main-visual-planner-12-step`. Nothing is pushed (CLAUDE.md: push only on explicit request; the PDF's
"push, check CI" step is therefore not done here).

## 0. What exists and is reused (PDF step 0)

| Existing | Reuse |
|---|---|
| `visuals/typography.py` (Pillow + raqm, RTL shaping, bundled fonts) | text layout of every graphic |
| `render/cards.py` `TimelineCard`, `DetectiveBoardCard` (engine draws a card object per shot: `frame(since, progress)`) | the engine path for graphics (`kind` in `CARD_KINDS`) |
| `mvp/cards.py` (typographic cards stored as photos, NEW_IMAGE) | replaced: cards become graphics, never pictures |
| `zoom_map/*` (open-data zoom video, Chromium) | map zoom stays; side panel and credit added |
| `visuals/generated.localize_texts` (language pipeline, cached) | all content text in the film language |
| `mvp/direct.py` slots, gaps, `add_*_cards`, `absorb_gaps` | planner decides use, place, length, cues |
| `mvp/coverage.py`, `stage_coverage` (before render) | plan-level gate |
| review queue UI + `MvpCandidate.final_status` | owner page for rejected / uncertain pictures |

Renderer decision: the PDF asks for "the method the app already uses". The app uses two: Chromium for maps and Pillow
cards for timeline / board / chapter cards. Text graphics use **Pillow** (same as `TimelineCard`, deterministic, RTL via
raqm, no browser start per card); maps stay on Chromium.

## 1. Requirements (R) — each gets a test (file::name) and live evidence

Owner prompt:

- **R1** Every moment has a picture, a video or one of the alternatives; no render while a moment is empty.
- **R2** Fallback order for an empty moment: real picture → video → map (only if the place is spoken) → document card →
  quote → newspaper headline → key facts → timeline → recap → conflict page → detective board → longer hold. Black is never rendered.
- **R3** Only if, after every alternative that the case's material allows, a moment is still empty: the job stops
  *before render* in a state that asks the owner, with a page listing those moments.
- **R4** Owner page shows the rejected and the uncertain pictures/videos (with the reason), owner can approve more.
- **R5** Conflict page and recap ("what we know so far") stay 3× longer than before (8 s → 24 s).
- **R6** Conflict page: "CONFLICT" label in red, title as the point in dispute, two sides (who says what, the opposing
  point, the source of each), status pill — presentation-page design.
- **R7** Recap board ("What we know so far"): Confirmed / Disputed / Still unknown cards + progress bar.
- **R8** Detective board carries the title "DETECTIVE BOARD" in all four languages (de DETEKTIVTAFEL, fa تابلوی کارآگاه, ar لوحة المحقق).
- **R9** Anything that is not a picture/video is never repeated in the film (a graphic key appears once).
- **R10** Text graphics never zoom, pan or move (static; reveals by cross-fade; only the timeline line draws).
- **R11** Maps correct: only where the place is spoken, open-data (not Google — licence), credit visible, fallback
  to the map still, never a map for an unlocated place.
- **R12** Newspaper headline cards: real headline of the time, source and date; from known newspapers found by search.
- **R13** Key-facts card: short facts, numbers, times as large text.
- **R14** Timeline: dates and events drawn as an animated line, presentation-page design.
- **R15** Every graphic is in the film's language; fa/ar are right-to-left and mirrored.
- **R16** All checks run **before** render (plan-level: completeness, repetition, language, spoiler, motion, sources);
  the render only executes a plan that already passed; nothing is left to inspect afterwards except the owner's watching.

PDF common rules:

- **R17** Graphics are made by code, never generated pictures; show real data only.
- **R18** Timing is the planner's: a graphic receives `duration_s` and `cues`; no minimum/maximum of its own.
- **R19** Interface per graphic: `can_render(data)`, `required_cues(data)`, `render(data, duration_s, cues, language)`;
  fewer cues than steps → the graphic says which steps it skips; never invents timing.
- **R20** 1920×1080, safe areas for a later 9:16; deterministic (same data+cues+language → same frames).
- **R21** Fonts in the repo (Barlow Condensed, Newsreader, IBM Plex Mono; Vazirmatn; Noto Naskh Arabic), licence recorded.
- **R22** No placeholder text: missing data → `can_render` says why → planner uses the next fallback.
- **R23** Spoiler firewall: nothing before the narration says it; no victim imagery/minors; names as the dossier allows.
- **R24** Credit footer on every graphic with an outside source (map provider, document, newspaper).
- **R25** Config in `config/parameters/` (keys named in the PDF); model names never in code; no new paid calls
  except the Brave search of R12 (budget ledger as usual); no migration unless a table is needed (none planned).
- **R26** Graphic types in the PDF order of value: Document card, Conflict page, Conflict board, Timeline, Map zoom, Recap board, Floor plan.
- **R27** Floor plan only when a real plan source exists in the case; otherwise `can_render` refuses (no invented layout).
- **R28** Tests per graphic: golden image per language, cue accuracy, spoiler, verbatim quote, no placeholder, board state, mirrored layout.
- **R29** One doc for these graphics (`docs/alternative_graphics.md`); small commits after the secret scan.

## 2. Design

`app/documentary/graphics/`
- `theme.py` colours/type scale from `config/parameters/graphics.json`; `fonts.py` mapping; `labels.py` + `config/graphics_labels.json`
  (fixed labels en/de/fa/ar — vetted once, not translated at render time).
- `base.py` `Graphic` ABC, `Cues`, `Rendered` (state images + cue times), `GraphicCard` engine object
  (`frame(since, progress)`: state k from cue k, cross-fade of `transition_fraction`).
- `document.py`, `conflict.py` (page + board), `timeline.py`, `recap.py`, `floorplan.py`, `headline.py`, `keyfacts.py`, `quote.py`,
  `maptext.py` (map-zoom side panel/credit data).
- `registry.py` type → class.

Planner (`mvp/direct.py` + new `mvp/alternatives.py`):
- `AlternativeOffer`s per beat: collected from validated evidence (document passages, conflicts, facts, timeline events,
  headlines, recap data) with `fact_id`s.
- Gap filling = ordered fallback chain (R2), seconds from config (`graphics.hold_seconds`), conflict/recap 24 s, may take
  neighbouring gap/hold time, never a pinned approved picture.
- Dedupe by content key across the film (R9). Cues = fractions of duration (config).
- Output shot `command: SHOW_GRAPHIC` with `graphic: {type, data, cues, duration_s}`; compose emits `kind: "graphic"`, `motion: NONE`.

Gate (`mvp/pipeline.py`): new `mvp_complete:<lang>` after direction and before production: re-checks completeness; if empty
moments remain → `OwnerDecisionNeeded` → job stops with `next_action` "Review pictures"; the review page gets the
candidate lists (rejected / needs_review) filtered to those moments. `stage_coverage` additionally blocks: black shot present,
graphic repeated, text shot with motion, language mismatch. `render` refuses a script that fails any gate.

Headlines (`mvp/headlines.py`): Brave queries from timeline events/dates; accept result only when the domain is a configured
newspaper (`config/parameters/headlines.json`), headline text from the result title (site suffix stripped), date known and
within the window of the event; stored as evidence on the plan; card shows source + date + original headline (quote kept in the
article's language; label localized).

Key facts (`graphics/keyfacts.py`): deterministic extraction of numbers/times/dates from ledger facts → big value + caption.

## 3. Order of work (loop; commit small after secret scan)

1. fonts + config + labels + theme + base + engine hook + tests (R15, R17–R22, R25)
2. document card (R26) → conflict page → conflict board → timeline → recap → key facts → headline → floor plan refusal/render
3. detective board title (R8), static text (R10), no repeat (R9)
4. planner: offers, fallback chain, durations, cues, dedupe (R1, R2, R5, R9, R18)
5. gate before render + owner stop + review page (R3, R4, R16)
6. maps (R11) verification in a real render
7. live: English film case 15 through the pipeline; report seconds and % per kind and remaining empty moments
8. docs (R29)

## 4. Second check (done after writing the plan)

Owner prompt, point by point:
1. Moments without pictures → R1, R2, R3, R4 ✔ (gate + owner page; owner asked only after alternatives)
2. Conflict and "what we know so far" 3× longer, conflict in red, "Conflict:" + placeholder, sources, who/what/opposing point → R5, R6, R7 ✔
3. "What we know so far" as a good presentation page → R7 ✔
4. Detective board title in all 4 languages → R8 ✔ (labels file)
5. Non-picture items never repeated; no zoom/move; unreadable after zoom → R9, R10 ✔
6. Maps crash / right text moments / Google → R11 ✔ (open data, reason for not using Google)
7. Newspaper headlines (real, source, date, known newspapers researched) → R12 ✔
8. Key facts card → R13 ✔
9. Timeline animated line as presentation page → R14 ✔
10. All checks before render → R16 ✔
11. Alternatives in the film's language, RTL for fa/ar → R15 ✔
12. Owner called only if black moments remain after all alternatives → R3 ✔

PDF, page by page: common rules → R17–R25; step order → R26 + section 3; Timeline (max 5 nodes visible, pan, no hidden event
before cue, 70-char label) → R14/R28; Map zoom (steps panel, credit, no map without a located place) → R11/R24; Floor plan → R27;
Document card (verbatim, source id, redactions, EXHIBIT only for exhibits) → R28; Recap (max 3/2/2, fact ids, spoiler) → R7/R23;
Conflict page (kinds, status never ahead of the narration, sides need source + fact id) → R6/R23; Conflict board (state kept
in plan, built from used pages only, 1–12 cards) → R6 + board state test.

Two items need an owner-visible decision and are logged, not guessed: (a) a quote from a document in another language stays in
that language (PDF says decide per case): kept original, label localized; (b) the PDF's push/CI step is skipped (no push without request).
