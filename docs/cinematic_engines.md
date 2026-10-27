# Cinematic engines

Eight explanatory engines the Main Visual Planner can use for a moment that has no suitable picture, video or other
graphic. They are graphics of the existing graphics system (`app/documentary/graphics/engines/`), offered through the
existing alternatives (`mvp/alternatives.py`), placed by the same `fill_beat`, drawn by the same renderer
(`SHOW_GRAPHIC` shots, `GraphicCard`), localized by the same language pipeline. Nothing parallel was added.

| Engine (`kind`) | Shows | Steps | Evidence it needs |
|---|---|---|---|
| `courtroom` | issue; prosecution (left) and defense (right); the evidence; the court's finding | prosecution, defense, [evidence], finding | both sides and a court finding |
| `custody` | one evidence item moving through discovery, collection, documentation, transfer, storage, analysis, court; documented gaps | one per documented stage | 2-5 documented stages |
| `unknowns` | KNOWN / UNKNOWN / DISPUTED; an optional dashed span between two documented moments | known, unknown, [disputed] | a known point and an open question (`Q`/disputed fact) |
| `forensic` | sample, how the method works (marked as educational illustration), reported result, limits | sample, method, result, [limits] | the examination and its reported result |
| `interrogation` | up to three dialogue turns (verbatim or marked paraphrase), earlier/later/record comparison | one per turn, [compare] | documented questions or answers |
| `sentencing` | charges and outcomes, aggravating/mitigating factors as documented, concurrent/consecutive, the final sentence, appeal | charges, [factors], final, [appeal] | charges and the sentence |
| `investigation_clock` | crime events above, investigation events below the line; quiet periods marked "no documented developments" with their length | one per event | dated `T` timeline entries |
| `evidence_check` | one item: what it shows, what it does not show, support/contradiction, documented assessment | item, [limits], [corroboration], assessment | an item with what it shows |

## Choosing

`mvp/engines.py::EnginePlanner` asks the agent `mvp.engine_plan` (prompt `prompts/documentary/mvp/engine_plan_system.md`,
role `mvp_engine_planner`) for five beats at a time. It sees only what the story has told by the beat (its reveals and
`viewer_knows`: facts `F`, dated timeline entries `T`, open questions `Q`, disputes `C`) and answers per beat with an engine
or `none`, why, which engines were eligible, what evidence is missing, and the engine's structured data. Most beats get none.
Answers are kept on the needs plan (`validation_json.mvp.engines`, `engine_decisions`) and reused until the beat's words or
facts change; every call is tracked and stops before a paid call when the case cap is reached.

## Factual rules (checked by code, not by the agent)

Every statement is `{text, basis, fact_id}`; `basis` is fact, allegation, witness, expert, finding, disputed, unknown or
(forensic method only) illustration. `validate()` refuses a statement when
the fact id is not in the case or was not told by that beat; a figure of the text is not in the cited fact; fewer than half of its words are;
a disputed or uncertain fact is shown as fact or finding; a "finding" cites a fact that records no court decision; an "unknown"
cites no open question; a verbatim interview quotation is not word for word in the record; a custody gap cites no fact that
says records are missing; a clock event has no dated timeline entry (its date is taken from that entry, never from the agent);
the evidence check has a percentage. An illustration has no figures and no fact and carries the label "educational
illustration - not case evidence". What fails is dropped, never shown. Every placed engine carries its provenance
(fact ids, their text, source ids) in its shot (`shot["engine"]`).

## Placement, repetition, length

Offers join the other alternatives (`planner.chain` in `graphics.json`; engines first). An engine is not shown again within
`engines.selection.min_beats_between_same_engine` beats, never with the same story (word overlap with an engine already
placed), at most `max_per_film` in a film, and the engine after another prefers a different kind. A shot is held like the
detective board: `base_seconds + seconds_per_step * steps`, clamped to 15-24 s (`engines.hold_*`); the steps are spread over
the shot (`engines.cue_span`), earlier steps dim while the newest is bright, at most three groups are bright at once.

## Look

One system: `ground`/`card`/`ink`/`amber`/`blue`/`red` of `graphics.json`, Barlow Condensed titles, Newsreader body, Plex Mono
tags; 16:9 pages drawn at 1920x1080 (like all graphics, scaled to the render size). Right-to-left languages mirror the whole page.
Labels exist in en, de, fa, ar (`labels`); texts of a placed engine go through the language pipeline once
(`evidence.localize_graphics`, `kit.texts_of`).

## Owner controls (Visual Planner tab -> "Cinematic engines")

`GET /api/cases/{id}/mvp/engines`, `GET .../engines/preview?beat=&step=`, `POST .../engines/override` with `disable`, `enable`,
`switch` (plan the beat again with a chosen engine), `replan`, `edit` (texts; re-validated against the cited fact), `timing`.
Choices are stored on the needs plan (`engine_overrides`) and applied to every rerun and language; they take effect when the
film is directed again (Resume).
