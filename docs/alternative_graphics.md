# Alternative graphics

What the film shows when no approved picture or video fits a moment. Design source: `docs/alternavie_options.pdf`;
plan and requirement list: `docs/ALTERNATIVE_GRAPHICS_PLAN.md`.

## Design reference

The looks follow the owner's six design pictures (timeline, map zoom, floor plan, document card, recap board, conflict
page, conflict board). Deliberate differences: a timeline node that has not been told is not drawn (spoiler rule; the picture greys it);
the conflict board shows a "N told" counter and the cards told so far, never empty slots or a total
(they would reveal how many conflicts are still to come); the recap draws only the sections that have items; the
"SAMPLE ..." footers of the pictures are design notes and never appear in a film; the route chip of the map picture
("[TIME] · LAST SEEN") needs a timed fact and is not drawn.

## Rules

- Made by code from stored evidence; never a generated picture, never placeholder text. If the data is incomplete
  the graphic says why (`can_render`) and the planner takes the next fallback.
- The **Main Visual Planner decides** use, place, length and reveal times. A graphic receives `data`, `duration_s`,
  `cues` and `language` and draws; it has no timing of its own.
- **Nothing that is not a picture is shown twice** (a fact id appears on one graphic only), **nothing moves** (pages
  fade in on their cues; only the timeline line draws), **nothing is shown before the story has told it**.
- Every text is in the film's language; Persian and Arabic are right-to-left and the whole page is mirrored.
  Fixed labels live in `config/parameters/graphics.json` (`labels`, en/de/fa/ar); content text goes through the
  language pipeline once, when the graphic is placed (`mvp/evidence.localize_graphics`), never at render time.
- Persian/Arabic shaping works with or without Pillow's raqm (`visuals/shaping.py`: arabic-reshaper + python-bidi).

## Types (`app/documentary/graphics/`)

| Type | Shows | Cues | Data comes from |
|---|---|---|---|
| `conflict` | one disputed point: title as a question, two sourced sides (who says what), status | left side, right side, status | `Contradiction` rows structured by agent `mvp.conflict` (two sides, each with a listed source) |
| `conflict_board` | every conflict page shown so far, one card each | the new card | the pages the planner actually placed, in order |
| `recap` | confirmed / disputed / still unknown + progress bar | one per section | facts told by then (`viewer_knows`), told conflicts, open questions (`opened_in`..`resolved_in`) |
| `timeline` | dated events on a line that grows node by node | one per event | the chapter plan's events (dates exactly as the fact states them) |
| `key_facts` | up to 3 figures (numbers, times) as large text | one per fact | stated facts with a figure; caption = agent `mvp.copy` line that keeps every figure |
| `quote` | a verified quotation with who said it | the quote | approved quotes; only complete quotations |
| `headline` | a real newspaper headline with newspaper and date | the headline | case sources that are listed newspapers (`headline.source_names`); earliest beat by agent `mvp.headline` |
| `document` | verbatim words of a court record with its type, issuer and date | highlight wipe | a verified quotation that stands word for word in an official stored source (`verify_verbatim`); otherwise the quote card |
| `floor_plan` | rooms, doors, entrances with numbered markers and a legend | one per marker | only a real plan in the case sources (rooms on a grid, from the stored plan); never invented. Case 15 has no plan source, so it is not offered |
| map page | the zoom-in map (65 % of the frame) with its level and scale label and the card naming the marked place, beside a panel of country → region → town → place | the camera's own zoom level | geocoder address; the map video is rendered at `zoom_map.panel.map_share` of the frame |

The detective board keeps its own renderer and now carries its name ("DETECTIVE BOARD" / DETEKTIVTAFEL / تابلوی کارآگاه /
لوحة المحقق) on a plate above the notes.

## Planning (`app/documentary/mvp/`)

`evidence.build_offers` gathers the evidence and builds **offers** (`alternatives.Offer`: kind, content key, the beats it may
appear in, data, seconds). `direct_film` assigns approved pictures first (relatedness-gated, repetition rules), then maps
(only where the place is spoken), then fills the remaining **bare stretches** with offers (`alternatives.fill_beat`):
longest stretch first, an offer that belongs to the beat before one that may drift in, never below an offer's minimum
seconds, every evidence id once. Conflict pages and recaps are held 24 s (config `hold_seconds`, three times the old
8 s card). Only what is still bare after that is held as a picture (never past `max_still_seconds`) or reported as a gap.

Config: `config/parameters/graphics.json` (`planner.chain`, `planner.min_seconds`, `hold_seconds`, `cue_fractions`,
`drift_beats`, per-graphic limits) and `config/parameters/main_visual_planner.json` (`coverage`).

## Before anything is rendered

1. `mvp_direct:<lang>` — every moment gets a picture, a video or a graphic; what is left is a **gap**.
2. `mvp_complete:<lang>` — with gaps left the run stops (`AwaitingPictureReview`, resumable) **before maps,
   production or render**. The owner opens *Review pictures*, which lists the empty moments and the pictures the planner
   rejected or was unsure about (`GET /api/cases/{id}/mvp/gaps`, `.../mvp/candidates?status=needs_review,rejected&beat=B..`),
   approves more, and resumes; direction runs again with what is approved.
3. `mvp_coverage:<lang>` checks the compiled script: coverage ≥ 0.995, no black over 0.5 s, no still held past its limit,
   no asset shown more often than allowed, **no graphic evidence shown twice, no text page that zooms or moves, no graphic
   in another language, no graphic dropped for not fitting**, host and music present. Only a script that passes is rendered.

## Tests

`tests/test_graphics.py` (golden thumbnails per graphic and language in `tests/golden/graphics`, determinism, cue accuracy,
stillness, mirroring, refusal of incomplete data), `tests/test_mvp_alternatives.py` (offers, placement, gate, checks, compose,
engine, owner view, localization), `tests/test_map_panel.py`. Refresh goldens with `UPDATE_GOLDEN=1`.
