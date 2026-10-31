# Film planning redesign — owner's spec (DRAFT, 2026-10-30)

Status: written from the owner's messages of 2026-10-30, analysed, owner's answers to the analysis recorded in section 9,
**not yet implemented** (waiting for the owner's "go" and the one open idea in section 9). Items marked **(confirm)** are
my reading of an unclear rule. Nothing here is pushed. Case 15 (English film) is the test case.

## 1. Cards: what exists

- Remove from the app entirely (code, builders, graphics, themes, golden images, tests, labels, prompts, config):
  `key_facts`, `unknowns`, `conflict_board`, `investigation_clock`, `custody`, `interrogation`, `courtroom`,
  `sentencing`, `floor_plan`.
- Remaining kinds: `map`, `detective_board`, `fact_card`, `recap`, `conflict`, `timeline`, `evidence_check`, `forensic`,
  `quote`, `headline`, `document`, plus `photo`/video (Main Visual Planner) and the Host.
- Every film has: a map, the detective board, a timeline. The 12-kind minimum is removed (no minimum number of kinds).
- Cards (with board and map) fill **30–40 %** of the film. **No card repeats.** `fact_card` at most 10 per film.
- Existing graphics code that is not removed keeps its per-kind theme (map and detective board keep their own look).

## 2. Card timing

- A card is shown **15–20 s**, never less than 15 s. The Main Planner may extend a card by **at most 5 s** (hard maximum 25 s) **(confirm)**.
- The Main Planner decides the time dynamically: it estimates how long a normal person needs to see and read all of the
  card's text, so the layout is flexible. Text streams **2× faster than today** (the stream schedule is config).
- A card whose text cannot be read in the maximum time is split or refused, never squeezed.

## 3. Timeline card

- Events with the same date are written together under **one** time point.
- Each next time point has a **later** date than the previous one (strictly increasing; out-of-order events are merged or
  dropped by the validator, never silently re-sorted into a wrong story).
- The timeline can grow and is laid out like a **snake**: a line that turns at the page edge and continues on the next
  row, so the whole timeline fits the page (mirrored for right-to-left languages).

## 4. Responsibilities: Toolkit and Main Planner

Toolkit (no strategist agents; the film and card strategist agents and their table are removed):
1. Estimates timing from the story text.
2. An agent analyses the text; for each part of the text it invokes an agent that creates the related card.
3. The detective board is made by the existing board agent, which continues its previous work.
4. Each card carries metadata: which part of the text it belongs to.
5. After creation an **inspector agent** (new: `detective.card_inspector`; the existing cutter, inspector and usable agents
   are not changed) checks each card for logical problems and writes them back; the toolkit fixes and the card is
   inspected again, up to a configured number of rounds; a card that still fails is dropped, not shown.
6. The planner has a **memory of the cards created so far** to prevent repeated content across cards.

Main Planner:
- Receives all cards and their metadata, arranges them together with the approved pictures and videos by the text, and may
  extend a card by up to 5 s. It never changes a card's content.

## 5. Pictures and videos: search, review, replan

Owner rules:
- Before rendering, **every** photo and video (approved and rejected) is shown to the owner. Only the owner changes an
  approval or rejection; the app never does. If the owner changes anything, the app **replans the timing**.
- Every photo and video has a **checkbox**. Checked by the owner means it must be used **100 %** in the film **(confirm what
  "100 %" means for a video: its whole approved cut, once)**.
- If an approved case photo that relates to the text of a gap is not used yet, general (open) photos may not be used for
  that gap.
- A gap that is still open after the last approval is covered by extending neighbours.
- An agent checks that every general (open) photo really fits this video, true crime and the text. Unrelated pictures are
  rejected (example: a linguistics transcript screenshot, "Illustrative image", for a murder case).

Process:
- **a)** The Main Planner searches. All gaps covered → owner's final approval → planner cuts photos and videos as before →
  replan (the planner never changes an owner decision). New material → owner approval → loop.
- **b)** Gaps not covered → owner review → cut as before → if anything changed the planner plans again (owner decisions
  untouched) → open general search → owner approval → replan → covered: end; not covered: related general search and the
  same loop with the owner.
- Existing search caps stay (5 new items per requirement, bare moment and entity). Existing cutting, logo/watermark
  handling and the inspector agents stay.

## 6. Decisions the owner gave

- The 30–40 % counts cards **plus** the detective board and the map.
- Repeats: the owner wrote "no card is allowed to repeat" and also answered "yes" to "may a card appear a second time".
  **Reading: no repeats (planner memory prevents them) (confirm).**
- Film length is not changed by these rules. Cost cap stays EUR 300 per case; case 15 spend was reset to 0 on 2026-10-29.

## 7. Analysis: problems and gaps found in the spec

1. **Repeat rule is contradictory** (above) — needs one answer.
2. **30–40 % may not be reachable without repeats.** About 55–95 cards of 15–20 s are needed for a 61-minute film; the
   case data supports roughly 25–30 distinct cards plus ~5 min of board and map ≈ 23 %. Proposed: aim for 30 %, never
   fill with repeats, report the shortfall and let pictures fill the rest.
3. **Fast text and 15–20 s hold:** the text finishes early and the card then waits. Accepted as intended.
4. **Owner's checkbox can conflict with other rules** (a picture that would show a fact before the words that tell it,
   blocked rights, not enough free time). The app must not silently ignore the owner: it shows the conflict and the owner decides.
5. **"The app never changes owner decisions"** conflicts with automatic rejection (a watermark that cannot be cut away,
   a duplicate). Proposed: the app may crop and trim an approved item but never rejects it; an unusable one is flagged to the owner.
6. **Loops need an end:** a limit of general-search rounds per gap and an end when no new candidate is found.
7. **A replan after an owner change must be cheap** (timing only, no new toolkit run).
8. **Card extension vs must-use pictures:** a must-use picture wins over extending a card.
9. **Review volume:** case 15 has about 3,300 approved and 1,600 rejected items; the review page needs paging and filters.
10. **Removal impact:** old stored plans and renders of other cases that use removed kinds, plus the dropped strategist
    table (`detective_strategies`, an Alembic migration) and config (`strategy_enabled`, `required_kinds`, `min_card_kinds`).
    The checkbox needs a new column (an Alembic migration, shown on live PostgreSQL).
11. **The map must exist** and needs a stored place; the current film has none (a bug to fix first). A case with no place at all is reported.
12. **Other languages** reuse the English cards; card timing is recomputed per language.
13. **Relatedness of a case photo to a gap** (rule: no general photos while a related approved one is unused) needs an
    agent judgement with a threshold; the existing topical match is the starting point.

## 8. Known bugs to fix with this work (found in the English film of job 56)

- No map in the finished film although one was planned.
- Sentencing cards showed an id instead of the charge (disappears with the card kind).
- About 10 pairs of identical cards (the new planner memory addresses this).
- The film has a 6 s black stretch, 3 frozen stretches and one bare moment of 14.8 s (B05).

## 9. Owner's answers to the analysis (2026-10-30, second round)

Decided:
- **Card time:** 15–20 s is the **hard limit for the toolkit**. The Main Planner may extend a card by **5 s more**, so the
  maximum on screen is 25 s. (Replaces the "(confirm)" in section 2.)
- **Owner's review is the safety net.** The owner checks every photo and video before any render and ejects (rejects)
  what must not be used. The app therefore does **not** flag or block conflicts with a checked (must-use) item, and the
  reveal-order firewall is not applied to an item the owner checked. (My reading of "ignore this rule" and "I will eject it".)
- **The one exception to "only the owner changes a decision":** when trimming and cutting cannot make an approved
  item usable (for example a logo that cannot be cut away), the **inspector** removes it. No other automatic rejection of
  an owner-approved item is allowed.
- **Review volume:** accepted. The research cap of 5 new items per requirement, bare moment and entity keeps the review small.
- **Migrations:** do them (drop `detective_strategies`; add the must-use checkbox column), each run and shown on live PostgreSQL.
- **Repeats and 30–40 %:** no card repeats (the planner's memory prevents it). Cards plus board plus map aim at 30–40 %;
  if the data cannot reach it, the app never fills with repeats and reports the shortfall (default, not contradicted by the owner).
- **"Used 100 %" for a video** means its whole approved cut, used once (default, not contradicted by the owner).

Open:
- **How loops with the owner end** (general-search rounds per gap): the owner will give a new idea. Until then the existing
  search caps are the only limit.

## 10. Implementation order (proposed)

1. Fix the missing map and the card duplicates in the current film (bugs).
2. Remove the nine card kinds everywhere, with the strategist agents, their table and config (migration, run live).
3. New toolkit: text-analysing agent, per-part card agents, board agent kept, card inspector agent with a fix loop,
   metadata per card, planner memory of created cards, card timing 15–20 s.
4. Timeline card: same-date merge, strictly later time points, snake layout (also right-to-left).
5. Main Planner: reading-time-based timing, 5 s extension, arranging cards with pictures, text streaming 2× faster.
6. Pictures and videos: must-use checkbox (migration), review of all items before every render, replan after owner
   changes, neighbour extension for gaps, "no general photo while a related approved photo is unused", relevance agent for general photos.
7. Live proof on case 15: English film, then the numbers (share of cards, durations, maps, no repeats).

## 11. Channel logo (added 2026-10-30)

- The channel logo must **always be on the screen** for the whole film.
- **No card contains a logo.** Adding the logo to the film is the **Main Planner's job** (an overlay over the film), not the card's.
- Today (`config/parameters/branding.json`, `app/documentary/render/logo.py`) the logo is drawn over pictures and clips only
  (`shot_kinds`: image, video), in short windows (at least 4 s, with fades), and "laps" every 600 s for 15 s; graphics, maps,
  host scenes and chapter/title cards have none. This changes: the planner places the logo window over the whole film,
  including over cards **(confirm: over cards too, as an overlay)**.
- Position stays: the top corner at the end of the reading direction (top right for English and German, top left for
  Persian and Arabic), clear of the subtitles. **Every card layout must keep that corner free.** The per-kind card
  decorations made on 2026-10-29 put tabs, dots and stripes in the top-right corner (`graphics/frames.py`); they must move
  (and mirror for right-to-left) so they never meet the logo.
- Checks: a render test that the logo is present in every frame range of the film, and a card test that the logo corner is empty.

## 12. The drawing hand (idea of 2026-10-30, option A: built in the app, no provider cost)

Owner's concept:
- A hand draws one or more pictures during the film, taken from the **main pictures of the case**, mostly of the victim
  or victims. A drawing must never be a spoiler; at its end it gives the audience a sense of the person or case.
- It is shown in a **small window on one side** of the screen while something else is on the screen. When there is nothing
  else to show (a **gap**), the small window grows to cover the whole screen. When the next item is planned it becomes
  small again and the film goes on.
- **A gap shorter than 4 seconds is covered by neighbours (extension), not by the drawing.** (Owner's decision.)
- Cost: none per film (the drawing is made in the app: a line sketch of an approved photo, revealed stroke by stroke with a
  hand image following the pen). One hand image is needed once.

My proposals (confirm):
1. **No spoiler:** the drawing starts when its subject is introduced in the text; the usual rules against showing a fact
   before the words that tell it apply to it. No injury or crime-scene photo is drawn; a portrait is respectful.
2. **Rights:** only photos whose licence allows a derived work, or that are public domain.
3. **Place:** over photos and clips only; it pauses over cards (cards fill the screen). It sits on the side away from the
   photo's main subject, above the subtitle strip, clear of the logo corner; it mirrors for right-to-left languages.
4. **Growing:** the window is scaled up with the sides filled by a blurred copy, never stretched; growing and shrinking take about 0.6 s.
5. **Drawing time** is independent of gaps: the hand keeps drawing while the window is visible; one drawing takes about 1–2 minutes.
6. It is a **planner element like the logo**, not a toolkit card; it does not count toward the 30–40 %. Gap order: approved
   case photos, then the drawing (when a gap is at least 4 s), then neighbour extension, then general photos (only if no
   related approved case photo is unused).

Open (owner): how many drawings per film and how long; which photos (victims only, or also the suspect and places);
a clean finished portrait or a rough sketch; whether it may also run over cards.

## 13. Drawing: look, speed, owner's choice and rendering (2026-10-30, after the demos)

The demos (`pencil_demo6.mp4` in the scratch folder, 19 min, local, no provider cost) settle the look. Reference values:

- **Pencil only, no hand**, black and white, tiny: about 5 px long on a 1280×720 frame (a quarter of the first demo, then a quarter again).
- **Drawn with real strokes**, not a scan: head outline, hair strokes, light tones, mid tones, the eyes/brows/nose/mouth, deep tones;
  straight hatching shaded by the photo's own tone (no invented lines, no caricature of a real person), pressure and grain,
  the pencil lifts between strokes. The identity becomes clear gradually.
- **Window:** flush in the corner with no gap and no border, **120×150 px on 1280×720** (9.4 % × 20.8 % of the screen).
  Top-left for English and German (logo is top-right); top-right for Persian and Arabic (logo goes to the left side, the
  painter to the top-right). **Decided by the owner.** The app's "illustration" label keeps its corner, which is the same corner as the
  painter's in every language; so while the window is visible the label is shown beside it (to its right in English and German,
  to its left in Persian and Arabic) so that it is never covered **(confirm)**.
- **Opacity:** over pictures the window is **60 % visible and 40 % watermark (transparent)**; when it covers the whole screen
  it is **100 %** (the sides filled with a blurred copy, never stretched). (The demo used 50 %; the owner wants 60 %. Reading
  confirmed by the owner.)
- A gap shorter than 4 s is covered by neighbours; from 4 s the window grows (about 0.8 s), then shrinks again.
- **Over cards the drawing is paused and hidden**; its progress continues from the same point afterwards.
- **Speed:** the base drawing of one portrait takes about 19 min (three times slower than the 6.3-minute demo). The planner may
  vary the speed to fit the timing, **by at most a factor of 2** (from half to double speed; reading confirmed by the owner).

Owner's choice of photos (UI to prepare - not built yet):
- In the photo/video review, the owner marks which **photos** are drawn (videos are never drawn) and gives each a **priority 1-10**.
  The app draws the first, then the second, and so on, perhaps only up to the fifth, as the film's time allows.
- Needs a new column for the draw priority (an Alembic migration, shown on live PostgreSQL) and a field beside the existing
  must-use checkbox; only photos whose licence allows a derived work can be chosen (the page says so).
- The drawing must not spoil: it starts when its subject is introduced in the text; no injury or crime-scene photo.

Rendering and parallel work (my proposal, to discuss):
- The drawing does **not** depend on the film's timeline. Render it **once** as a *progress video* of the bare canvas
  (frames at fixed small steps, plus the pencil position per frame) as soon as the owner has chosen the photo; it can run in
  parallel with everything else (toolkit, planner, picture search, host). When the film's timing is known, the compositor
  only picks frames by progress (pausing over cards, speeding up or slowing down by at most 2×) and blends the window.
  This makes the 11-minute drawing render independent of the film render.
- The film render starts when the timing of the film **without avatar and cards** is determined (the narration timeline).
  Cards, host scenes, maps and the drawing are made in parallel and composited at the end.
- Further idea: render the film in segments (for example per chapter) and join them, so finished parts render while later
  parts are still being planned. Needs care for the logo, the subtitles and the transitions at the joints.

## 14. Research and approval process (owner, 2026-10-30)

Order of the stages, as the owner described them:

1. **Plan.** The Main Planner has the cards (from the toolkit) and plans the picture research.
2. **Search (existing search stays: Brave, YouTube videos, cutting).** Each **identity** (person, place, object of the film) may
   return at most **5 photos and 5 videos**. Downloading in batches, or more than 5 photos and 5 videos per identity, is **not allowed**.
3. **New: photos and clips from other true-crime channels' videos on YouTube.** The app must be able to take good-quality
   photos (frames) of scenes related to the case from such videos, and to cut spans out of them as videos too. The related
   agent selects a span (start and end) and the app cuts it. Per identity: up to **20 photos (frames) and 5 videos (clips)** (changed by the owner, see section 18).
   Trimming and cutting happen later (stage 6); audits and all other checks stay as they are.
4. **Inspection** (existing inspectors, unchanged), then rejection and approval by the app.
5. **Pause 1: owner review.** The owner approves or rejects every photo and video.
6. **Cut and trim.** The Main Planner cuts and trims where needed, then **inspects again**. This inspector is the **only** one
   allowed to change an owner decision (it removes an item that trimming and cutting cannot make usable).
7. **Pause 2: owner review of the result.** The app shows the owner every trimmed and cut video and photo; the owner approves or rejects.
8. **From here on nobody but the owner may change an approval or a rejection.**
9. **Plan again.** A gap **shorter than 4 s** is covered by neighbours; the **drawing film(s)** (the pencil portrait) cover the other gaps.
10. **Cards:** the toolkit tries **twice** to reach at least **40 %** of the film with cards (board and map counted); if it does not
    get there it goes ahead anyway. **No card is repeated.**

## 15. Open points in the research and approval process (points 1-4 answered in section 16)

1. **Caps:** do the 50 frames and 5 clips per YouTube video count toward the 5 + 5 per identity? My reading: the cap counts the
   **source videos downloaded per identity (at most 5)**; frames and clips are cut from them (50 and 5 per source video)
   and only the approved ones are kept. **(confirm)**
2. **Card share:** earlier "30 to 40 %", now "at least 40 %". My reading: **target 40 % or more, no upper limit**, two attempts,
   a shortfall is accepted. **(confirm)**
3. **General open search** (Wikimedia Commons and archives, with the relevance agent that rejects unrelated pictures such as
   the linguistics screenshot, and the rule "no general photo while a related approved case photo is unused"): is it **still part**
   of the process, for example inside stage 2? The new description does not mention it. **(owner decides)**
4. **After pause 2 there is no new search.** If the owner rejects a cut item, its gap is covered by neighbours and the drawing,
   not by new material. **(confirm)**
5. **Rights:** frames and clips from other channels' videos are their authors' work. The app keeps its existing rights
   classification (preview-only until cleared); showing them in a published film is the owner's decision and risk. The credit
   overlay the app already draws stays.
6. **Cut-out wording:** "select a span and cut it" is read as: pick the start and end of a good scene and cut that span out as a
   clip (the existing moment-cutting does this); the logo and watermark cut-away with the second check stays.


## 16. Owner's answers to section 15 (2026-10-30)

1. **Caps:** **5 per identity** from the other references (the existing search), and **further** from YouTube **50 photos and 5 videos**
   (per available video, as in section 14; a separate budget). Replaces the earlier reading that YouTube frames count toward the 5 + 5.
2. **Card share: 30 to 40 %** (cards with the board and the map). Not "at least 40 %". The toolkit tries twice to reach at least 30 %;
   if it does not, the app goes ahead; it does not aim above 40 %. No card is repeated.
3. **The general open search is dropped** (Wikimedia Commons and archives, its relevance agent `mvp.general_check`, and the rule
   about unused related case photos that went with it). Per the project rule, its code is removed once the new process works.
4. **The flow, final:**
   1. The planner plans (it has the cards) and researches (5 per identity from references; 20 photos and 5 videos per identity from YouTube).
   2. Inspection and the app's approval/rejection; the planner plans nothing new here.
   3. **Owner review 1:** the owner approves or rejects everything.
   4. The planner trims and cuts; the inspector checks again (the only one allowed to change an owner decision).
   5. **Owner review 2:** the UI shows **only the items that were trimmed or cut** (not everything again); the owner approves or rejects them.
   6. The planner plans again (gaps under 4 s by neighbours, longer gaps by the drawing film) and the app renders.
   Nothing but the owner changes an approval or a rejection after review 1; there is no new search after review 2.

Still open: the rights note (point 5 of section 15, the owner's decision) and the reading of "select a span and cut it" (point 6).

## 18. Owner's decisions of 2026-10-30 (after the implementation plan)

1. **YouTube budget: 20 photos (frames) and 5 videos (clips) per identity** - not per available video, and not 50. This replaces the
   numbers in sections 14 and 16 (the 5 per identity from the other references stays, as a separate budget). Downloading in batches
   or above these numbers stays forbidden.
2. **The "illustration" label** is shown beside the drawing window (agreed).
3. **No fixed number of portraits and no fixed total drawing time.** How many portraits are drawn follows from the photos' complexity, the
   drawing speed (0.5x to 2x, set by the planner) and the length of the film: the planner takes the owner's priority order (1, 2, 3, ...)
   and draws as many as the visible time and the gaps allow.
4. **The drawings are used for all languages.** A drawing is a sketch of a photo and has no text, so the **progress video is rendered once
   per photo and reused in every language**. Per language only these change: the corner (top-left for English, German, Spanish and Turkish;
   top-right for Persian and Arabic, where the logo is on the left), the side of the "illustration" label, the card pauses, the gaps and
   the speed (0.5x to 2x) the planner chooses for that language's film. If a language's film is too short or too long for the speed range,
   fewer or more portraits are used (the priority order decides).


## 19. Owner's decisions of 2026-10-31 (after the first full film with the drawing)

1. A gap shorter than **8 s** is covered by the neighbouring pictures (within the 27 s a picture may stay); from **8 s** the portrait fills the
   screen. A black stretch the neighbours could not cover (too long for their limit) that is at least 3 s long is drawn rather than left black.
2. The pencil is **4 times bigger** (18 x 3.6 px on a 1280-wide frame, was 4.5 x 0.9).
3. The drawing is **3 times faster**: one portrait takes 380 s of drawing time at normal speed (was 1140 s). Portraits already made keep their
   video; the compositor plays them at the planner's pace, so nothing has to be made again.
4. **Gaps one after the other are one gap**: the window grows once at the first and shrinks once after the last (it does not return to the corner
   between them). Black stretches next to each other are merged in the script too.
5. **Person photos at the first mention**: an inspector checks, before the planner plans, which persons the text names for the first time,
   finds their approved photos that are no spoiler there, saves them in a table (text position, photo) and hands the table to the main planner,
   which uses them where its own rules allow.
