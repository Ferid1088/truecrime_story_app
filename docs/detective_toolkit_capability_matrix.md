# Detective toolkit — capability matrix and implementation map (Section A audit)

Audited directly against the current checkout (see Baseline section). This file is the completion
proof for checklist entries A1, A2, A3. The Main Visual Planner (MVP) is the existing 12-step
architecture this feature extends (`docs/MAIN_VISUAL_PLANNER_12_STEP_IMPLEMENTATION.md`); `mvp.*`
module names below are MVP code, not a separate system.

## Pipeline trace (final text -> render -> review)

1. **Final narration -> editorial blueprint.** `NarrativeDirector.create` (`app/agents/blueprint.py:26`)
   reads the approved `StoryVersion` text and calls the director prompt
   (`prompts/documentary/blueprint/director_system_prompt.md`) through
   `app.documentary.blueprint._call_director`/`validate_blueprint` (`app/documentary/blueprint.py`).
   Output is an `EditorialBlueprint` of ordered BEATS (paragraph ranges, reveals, opens/answers,
   purpose, human_focus).
2. **Reveal graph (MVP Step 2).** `app/documentary/mvp/pipeline.py:ensure_graph` reuses or builds a
   validated `RevealGraph` via `app/longform/service.py:build_reveal_graph_payload` /
   `create_reveal_graph`, persisting typed nodes/edges and first-reveal beat per node.
3. **Visual needs (MVP Step 4).** `stage_needs` -> `app/documentary/mvp/needs.py:plan_needs` turns
   beats + evidence + reveal graph into `MvpRequirement` rows (entity/claim-scoped, graph-checked).
4. **Candidate library, verification, rights (MVP Step 5).** `mvp/library.py:build_candidates`,
   `mvp/verify.py:verify_asset`, `mvp/research.py` gather and vision-judge candidate assets; only
   `final_status == "approved"` assets are usable downstream.
5. **Evidence-built alternatives + engines (MVP Step 7).** `mvp/evidence.py:build_offers` assembles
   `Offer`s from stored facts/timeline/questions/contradictions/quotes/headlines
   (`mvp/alternatives.py`) and from the eight cinematic engines (`mvp/engines.py:build_engine_offers`,
   prompt `prompts/documentary/mvp/engine_plan_system.md`), each offer backed by fact IDs and checked
   by `engines.py:check_item`/`validate`.
6. **Direction (MVP Step 8).** `stage_direct` -> `mvp/direct.py:direct_film` / `_direct_pass` cuts each
   beat into slots, assigns approved photos/clips (agent `model_director` + deterministic
   `baseline_assign`), places maps (`add_map_cards`), then fills remaining gaps with alternatives/engine
   offers (`alternatives.py:fill_beat`), then moment clips, topical matches, repeats, neighbour-cover,
   and the `satisfy.py:RequirementPass` last-resort fill.
7. **Graphics materialization.** `app/documentary/visuals/generated.py:materialize` +
   `mvp/evidence.py:localize_graphics` render/localize the chosen graphic shots.
8. **Production composition (MVP Step 9).** `app/documentary/production/script.py:compose` builds the
   final per-language shot timeline: chapter cards, detective-board card insertion
   (`insert_detective_board_cards`), zoom-map holds, then `graphics/shots.py:finalize` resolves each
   graphic's cue fractions into real seconds against the compiled timeline.
9. **Rendering.** `app/documentary/render/engine.py` plays shots, including `MapPanel`
   (`app/documentary/graphics/mappanel.py`) for zoom maps and `GraphicCard`
   (`app/documentary/graphics/shots.py`) for every registered graphic kind.
10. **Coverage / review (MVP Step 6, 10-11).** `mvp/coverage.py:measure`/`verdict` compute temporal and
    requirement coverage and graphic-repetition checks; `mvp/review.py` and
    `mvp/pipeline.py:stage_review` gate owner review.

## Capability matrix

Columns: Tool/Engine ID · Renderer file · Caller(s) · UI control · Input contract summary · Status · MVP
contract fit · Integration work needed for the detective toolkit.

All graphic renderers share the common `Graphic` interface (`app/documentary/graphics/base.py`):
`can_render(data) -> str|None`, `required_cues(data) -> int`, `draw_state(data, language, step)`; data is
plain JSON (statement dicts, fact IDs, dates). "Registered" = present in
`app/documentary/graphics/registry.py:GRAPHICS`. "Reachable" = at least one production code path builds
an `Offer`/shot that uses it. "Dead" = registered but no caller ever constructs it.

| # | Tool | Renderer | Caller(s) | UI control | Input contract | Status |
|---|------|----------|-----------|------------|-----------------|--------|
| 1 | Map / zoom map | `app/documentary/graphics/mappanel.py` (`MapPanel`) + `app/documentary/zoom_map/*` | `mvp/direct.py:add_map_cards` builds `SHOW_MAP` shots; `render/engine.py:_map_page` draws | none | place name + region string; camera curve from zoom_map subsystem | Reachable |
| 2 | Detective board | `app/documentary/detective_board.py` + `render/board_cards.py` (card draw) | `detective_board.py:select_cards`/`appearances`; inserted by `production/script.py:insert_detective_board_cards` | none | blueprint beats (reveals/opens/answers/purpose/human_focus); language titles via `app/agents/detective_board.py` writer call | Reachable |
| 3 | Fact / recap board | `app/documentary/graphics/recap.py` (`kind="recap"`) | `mvp/alternatives.py:recap_offers` | none | confirmed/disputed/unknown fact-id groups, `every_beats` cadence (config) | Reachable |
| 4 | Known / unknown panel | `app/documentary/graphics/engines/evidence.py` (`MapOfUnknowns`, `kind="unknowns"`) | `mvp/engines.py` engine-plan pipeline | `engines-panel.tsx` (shared 8-engine panel) | known/unknown/disputed statement lists, `basis`+`fact_id` per item | Reachable |
| 5 | Conflict comparison | `app/documentary/graphics/conflict.py` (`ConflictPage`, `kind="conflict"`) | `mvp/alternatives.py:conflict_offers` | none | one structured contradiction (`sides`, `topic`, `description`) | Reachable |
| 6 | Conflict board | `app/documentary/graphics/conflict.py` (`ConflictBoard`, `kind="conflict_board"`) | `mvp/alternatives.py:board_offer`, triggered by `board_due` in `_direct_pass` | none | cards = conflict pages already shown this round | Reachable |
| 7 | Timeline | `app/documentary/graphics/timeline.py` (`kind="timeline"`) | `mvp/alternatives.py:timeline_offers` | none | dated events (fact_id, date, label, claim), deduped via `same_event_once` | Reachable |
| 8 | Investigation clock | `app/documentary/graphics/engines/story.py` (`InvestigationClock`, `kind="investigation_clock"`) | engine-plan pipeline | `engines-panel.tsx` | crime/investigation track events, each a dated `T` timeline id | Reachable |
| 9 | Fact card | `app/documentary/graphics/factcard.py` (`kind="fact_card"`) | `mvp/alternatives.py:fact_card_offers`, called from `mvp/evidence.py:build_offers` with generated `sentences` | none | 1-N confirmed-fact short sentences, grouped per beat | Reachable (last-resort priority) |
| 10 | Key facts card | `app/documentary/graphics/keyfacts.py` (`kind="key_facts"`) | `mvp/alternatives.py:key_fact_offers` | none | extracted figure/value + caption line per confirmed fact | Reachable |
| 11 | Document highlight | `app/documentary/graphics/document.py` (`DocumentCard`, `kind="document"`) | `mvp/alternatives.py:document_offer` (only when a quote verifies verbatim against a stored official source) | none | document id/type/issuer/date + verified quote span | Reachable (narrow: verbatim+official-source only) |
| 12 | Quotation card | `app/documentary/graphics/quote.py` (`kind="quote"`) | `mvp/alternatives.py:quote_offers` | none | fullest verified quote text + optional speaker, fact_id | Reachable |
| 13 | Newspaper headline | `app/documentary/graphics/headline.py` (`kind="headline"`) | `mvp/alternatives.py:headline_offers`, placement from `mvp/headlines.py` | none | parsed source headline/date/publisher + placement beat window | Reachable |
| 14 | Floor plan | `app/documentary/graphics/floorplan.py` (`kind="floor_plan"`) | **none found** — registered in `registry.py` only | none | rooms/entrances/legend (per docstring/config `floor_plan`) | **Dead** — no offer builder anywhere constructs `kind="floor_plan"`; fully implemented renderer is unreachable in production |
| 15 | Evidence check | `app/documentary/graphics/engines/trial.py` (`EvidenceCheck`, `kind="evidence_check"`) | engine-plan pipeline | `engines-panel.tsx` | item + establishes/not_establishes/supported_by/contradicted_by/assessment | Reachable |
| 16 | Chain of custody | `app/documentary/graphics/engines/evidence.py` (`ChainOfCustody`, `kind="custody"`) | engine-plan pipeline | `engines-panel.tsx` | item + 2-5 documented stages, optional gap_after | Reachable |
| 17 | Forensic examination | `app/documentary/graphics/engines/evidence.py` (`ForensicExamination`, `kind="forensic"`) | engine-plan pipeline | `engines-panel.tsx` | exam type, sample/method/result/limits statements | Reachable |
| 18 | Interview analysis | `app/documentary/graphics/engines/story.py` (`InterrogationAnalysis`, `kind="interrogation"`) | engine-plan pipeline | `engines-panel.tsx` | 1-6 question/answer turns, optional earlier/later compare | Reachable |
| 19 | Courtroom reasoning | `app/documentary/graphics/engines/trial.py` (`CourtroomReasoning`, `kind="courtroom"`) | engine-plan pipeline | `engines-panel.tsx` | issue + prosecution/defense statements + finding | Reachable |
| 20 | Sentencing explanation | `app/documentary/graphics/engines/trial.py` (`SentencingExplanation`, `kind="sentencing"`) | engine-plan pipeline | `engines-panel.tsx` | charges, aggravating/mitigating, arrangement, final sentence | Reachable |
| - | Photos / clips | `VisualAsset` library, `mvp/direct.py` (`baseline_assign`, `model_director`) | primary fill path in `_direct_pass`, before any graphic | Review-pictures UI (asset approval, not this feature) | approved asset + role/entity + clip bounds | Reachable (primary path) |
| - | Host segments | `app/documentary/host.py`/`host_scenes.py`, `production/host_timeline.py` | `mvp/pipeline.py:stage_host` | Host plan UI (separate feature) | planned `HostScene` rows, produced before direction | Reachable, but host placement is independent of the detective plan. **Owner correction, 2026-10-28: this independence is intentional, not a gap to close by giving the detective toolkit control over Host.** Host remains the Main Visual Planner's own authority (`docs/MAIN_VISUAL_PLANNER_12_STEP_IMPLEMENTATION.md` Step 4); the detective toolkit must never choose, place, or request a host appearance as a tool action. `app/documentary/detective/tool_catalog.py` no longer lists `"host"` as a dispatchable tool_id, so `schema.py`'s existing unknown-tool_id rejection catches it if the agent's prose-level mention of "host shots" (required verbatim prompt wording) ever leads it to propose one. Passing the Host system's already-placed scenes to the director as read-only planning context (so it can schedule around them) remains real, deferred work for Section H. |

## The authoritative hand-off seam: Detective Director -> MVP Step 4 (`needs.py`)

The data flow the detective toolkit requires is **Detective Director plan -> MVP consumes it -> MVP
runs its later steps** (research, verification, selection, direction, manifest) — not a parallel or
competing planner. Today, MVP invents its visual-needs requirements straight from the blueprint with
no detective input at all; this is the exact seam that must be replaced/fed:

- **Current seam:** `app/documentary/mvp/needs.py:plan_needs` (lines 240-274).
  - Line 247-251: reads the `EditorialBlueprint` and evidence pack directly (`build_pack`), with no
    detective-plan object anywhere in the call signature.
  - Line 252: `payload = planner_input(case, blueprint, pack, sources, view, seconds)`
    (`planner_input` at `needs.py:71`) assembles beats/graph/facts/timeline — but never a detective
    scene aim, active thread, or chosen-tool preference.
  - Line 255: `run_agent("mvp.needs", ...)` — a **second, independent** model call
    (`prompts/documentary/mvp/needs_system.md`) that decides, from the beats/graph/evidence alone,
    what the viewer must see and which `function`/`role_needed`/`alternatives` each beat needs. This
    is the model that currently plays the role the Detective Director (Section C) must own.
  - Line 258/210: `validate_needs(...)` -> `store_requirements` persists the result as
    `MvpRequirement` rows (`node_key`, `function`, `entity_key`, `role_needed`, `priority`,
    `acceptable_roles`/`alternatives`/`need`). Every downstream MVP step — `direct.py` (`approved_items`,
    `requirement_first`), `alternatives.py`/`engines.py` (offer building), `satisfy.py`
    (`counted_requirements`) — consumes `MvpRequirement` rows, not the blueprint or narration directly.
- **What must change:** `MvpRequirement`'s existing fields are the real contract MVP's downstream steps
  already consume; the Detective Director's scene/tool plan (Section C: scene aim, active
  thread/question IDs, chosen tool per narration span, content bindings) must be translated into
  `MvpRequirement` rows at this exact point, either by (a) replacing the `mvp.needs` agent call with a
  deterministic translation of the Detective Director's output into the existing `requirements` schema,
  or (b) keeping `plan_needs` only as a fallback/gap-filler for spans the detective plan did not cover,
  clearly subordinate to it. Either way, `needs.py:plan_needs` (and its `mvp.needs` prompt) must stop
  being the place where "what should be seen here" is independently decided once Section C exists —
  that decision has to come from the Detective Director's map/direct output (Section C's `scenes[].actions`),
  with MVP Steps 5-9 (`library.py`, `verify.py`, `direct.py`, `alternatives.py`, `engines.py`,
  `production/script.py`) executing it rather than re-deriving it.
- **Secondary seam, same direction:** `mvp/engines.py:EnginePlanner.decide` (its own `mvp.engine_plan`
  agent call, `engine_plan_system.md`) independently re-decides "which beat gets which of the eight
  engines" from facts alone — this is the same inversion one layer down (Obstacle 2 below) and must
  also become consumption of the detective's tool choice rather than a second independent decision.
- **MVP contract fit:** the reveal-graph gate (MVP Step 2, `RevealGraph`/`RevealNode`) and the
  rights/verification gate (MVP Step 5, `MvpCandidate.final_status`) are already graph-authoritative and
  do not need to change — the detective plan must flow *through* them (a detective-chosen photo/graphic
  still needs an approved candidate and still obeys `reveal_blocked`), not around them. The gap is purely
  that nothing upstream of `needs.py` yet exists to author `MvpRequirement`s from detective intent; MVP's
  verification/rights/coverage machinery (Steps 5-7, 6) is reusable as-is once that upstream object exists.

## Conflicting rules found (Section A obstacles)

All seven call sites below were re-read in the current checkout (HEAD `ea14734`, see Baseline) and are
confirmed **still present**, matching the doc's description, and unchanged since the cited baseline
tree `646bbee` (none of these files appear in the `646bbee..HEAD` diff — see Baseline section).

### 1. Photos/maps assigned before `fill_beat` uses graphics

`app/documentary/mvp/direct.py`, function `_direct_pass` (lines 594-736). Per beat: `chosen` is filled
from approved photos first (agent `model_director` / `baseline_assign`, lines 650-690), then
`add_map_cards(shots, ...)` places maps (line 696), then only afterwards
`AL.fill_beat(shots, window_len, idx, offers, state, card_tail(window))` (line 699) lets graphics/engine
offers into whatever stretches are still bare. Quote (direct.py:694-700):
```python
add_map_cards(shots, extras.get(bid) or {}, window_len, places, mapped, region)
add_date_overlay(shots, extras.get(bid) or {})
idx = beat_order.get(bid, 0)
AL.fill_beat(shots, window_len, idx, offers, state, card_tail(window))
AL.extend_road(shots, window_len, idx, offers, state, card_tail(window))
```
The real order is: approved photos (`chosen`/`baseline_assign`) -> maps (`add_map_cards`) -> date
overlay -> graphics/engine offers (`AL.fill_beat`) -> road continuation (`AL.extend_road`). Confirmed
exactly as the doc describes: graphics only ever get leftover time, never a planned moment of their own
alongside a plentiful photo.

### 2. Engine prompt: one engine per beat, "most beats should get none"

`prompts/documentary/mvp/engine_plan_system.md`, line 1 and line 52. Quotes:
```
you decide whether ONE of eight cinematic engines would make the moment clearer than a picture
```
```
Do not choose an engine unless it clearly makes the beat easier to understand than a picture would. Most beats get "none".
```
Confirmed exactly as the doc describes — this is a hard editorial ceiling (at most one engine, and an
explicit bias toward none) baked into the prompt text itself, independent of what the Detective Director
would decide.

### 3. `check_item` requires "unknown" basis to cite a question/disputed fact

`app/documentary/mvp/engines.py`, function `check_item` (lines 112-130), specifically lines 127-128:
```python
if basis == "unknown" and ref.category != "question" and not ref.disputed:
    return f"{path}: {ref.id} does not record an open question"
```
Confirmed present: a statement with `basis: "unknown"` is rejected unless the fact it cites is already
categorized `"question"` (a stored `Q` row) or already marked disputed/uncertain — exactly the "must
reference a question/disputed fact" restriction the doc calls out. A text-grounded, not-yet-explained
detail with no such research row cannot be shown as an unknown.

### 4. `used_items` / `graphic_checks` prohibit repeating a fact across graphics

Two call sites, both confirmed:
- Selection-time, `app/documentary/mvp/alternatives.py`, function `usable` (lines 397-400):
  ```python
  def usable(o: Offer, idx: int, remaining: float, state: State, said: str = "") -> bool:
      return (not o.placed and (o.after is None or o.after.placed) and o.key not in state.used_keys
              and o.first <= idx <= o.last + state.relax.get("extra_drift", 0)
              and o.min_seconds <= remaining + 1e-6 and not (o.items & state.used_items)
              ...
  ```
  `not (o.items & state.used_items)` means once any fact ID has been shown in one graphic, no other
  graphic offer citing that same fact ID can ever be selected for the rest of the film.
- Review-time (hard publication blocker), `app/documentary/mvp/coverage.py`, functions `graphic_checks`
  (lines 101-121) and `verdict` (lines ~200-206):
  ```python
  if graphics["repeated"]:
      blocking.append(f"{len(graphics['repeated'])} graphic item(s) shown more than once")
  ```
  `graphic_checks` flags any fact ID appearing in more than one graphic shot as `repeated`, and `verdict`
  turns that into a publication-blocking finding. Confirmed exactly as the doc describes, at two
  enforcement layers (selection and post-hoc validation) — a justified return/comparison/update is
  indistinguishable from redundant replay to both.

### 5. `beat_view` truncates narration and references

`app/documentary/mvp/engines.py`, function `beat_view` (lines 235-248). Quote (line 244, 246):
```python
ids = (list(dict.fromkeys(reveals + [f for f in usable if f not in stamps]))[:20] + stamps)[:28]
...
"said": ev.narration.get(beat["id"], "")[:900],
```
Confirmed present: the spoken text fed to the engine planner is hard-truncated to 900 characters, and
the facts it may use are capped at 20 (plus up to 28 total with timeline stamps). A decisive clause or
qualifying fact past those limits is silently invisible to the engine-choice agent.

### 6. Board selection privileges a dated event over other content in the same beat

`app/documentary/detective_board.py`, function `select_cards` (lines 73-120), specifically the per-beat
loop at lines 91-99:
```python
for bid in order:
    event = by_fact_beat.get(bid)
    if event is not None:
        candidates.append({"kind": "time", "source_fact": event["id"], "beat_id": bid})
        continue
    if bid in opened_by_beat:
        candidates.append({"kind": "open_question", "source_fact": opened_by_beat[bid][0], "beat_id": bid})
        continue
    kind = KIND_BY_PURPOSE.get(str(purposes.get(bid)))
    ...
```
and the docstring at lines 76-78 states the rule explicitly: *"One card per beat at most (a beat that
both reveals a dated event and is a contradiction gets the dated-event card — dates are the stronger,
more concrete claim)."* Confirmed exactly as the doc describes: a beat with a dated event never reaches
the open-question or purpose-based (person/place/object/contradiction) branch at all, by construction
(`continue`), regardless of what else that beat means investigatively.

### 7. Graphic cues are fraction/schedule-driven; compilation inserts boards and adjusts visuals afterward

Two confirmed parts:
- Fractions resolved only at final compile time: `app/documentary/graphics/shots.py`, `cue_fractions`
  (lines 22-34, stores fractions of shot length) and `finalize` (lines 84-102), which only turns
  fractions into seconds once a shot has a final `start`/`end` from the compiled timeline — a graphic
  that no longer fits at that point is silently dropped (`shots.remove(shot)`, line 95).
- Compilation inserts boards and physically cuts existing shots afterward:
  `app/documentary/production/script.py`, `compose` calls `insert_detective_board_cards` (line 1244)
  **before** `finalize_graphics` (line 1245). `insert_detective_board_cards` (lines 826-879) calls
  `_carve(shots, card_shot)` (line 875), and `_carve` (lines 918-958) splits/shortens whatever
  photo/video/graphic shot the new board card lands on top of:
  ```python
  def _carve(shots: list[dict], card: dict) -> None:
      """Put `card` on the timeline, cutting back what it covers."""
  ```
  Confirmed exactly as the doc describes: the detective-relevant content (a photo, a graphic already
  placed by `_direct_pass`) can be shortened or removed by a later compilation pass that knows nothing
  about the content binding/anchor that put it there — there is currently no mechanism carrying a
  narration anchor through `_carve`/`finalize` to guarantee the cut content's meaning/timing survives.

## Baseline

- `git log -1` (current checkout, branch `feature/detective-toolkit-planner`): HEAD = `ea147342ab0e414e4a9b2297d76844436330c3db`
  ("docs(detective-toolkit): add the A-K implementation prompt plus its tracking checklist and
  machine-readable checkpoint, all rows NOT_STARTED").
- Doc-cited baseline tree: `646bbeea9a2e2639fac8e679bb3308ded0a80fe5` ("feat(planner): the case timeline
  road reaches its end...").
- HEAD is **two commits** ahead of that baseline, not one of which is the docs-only commit:
  1. `8548072` ("feat(planner): the statement card gets its own prompt file, and the fact-card,
     alternatives, direct and pipeline stages pick up the matching wiring...") — real code changes to
     `app/documentary/mvp/{alternatives,direct,evidence,pipeline}.py`,
     `app/documentary/graphics/factcard.py`, `app/documentary/production/script.py`, plus config/tests.
  2. `ea14734` (this task's own docs commit — the implementation prompt, checklist, checkpoint JSON;
     no application code changed).
- `git diff 646bbee HEAD --stat` confirms none of the seven obstacle files above
  (`engines.py`, `detective_board.py`, `coverage.py`, `shots.py` cue logic, `engine_plan_system.md`)
  changed in that window; the only touched planner files (`alternatives.py`, `direct.py`, `evidence.py`,
  `pipeline.py`, `production/script.py`) changed in ways unrelated to the obstacles (statement-card
  grouping, `cover_last` threading, timeline same-event dedup) — the obstacle behavior described above is
  verified against the code as it stands at current HEAD, and is unchanged from the doc's cited baseline.
