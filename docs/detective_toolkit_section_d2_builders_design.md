# Section D2 — the remaining dispatch builders (closes most of D2 and 9 of the 17 open T0x rows)

Follow-up to Section D (`docs/detective_toolkit_section_d_design.md`), which built direct-dispatch
builders for `fact_card` (T09) and `floor_plan` (T14) only, plus Section D's own `timeline` (T07)
fix (sort-order, no dispatch builder). This pass adds real, render-proven builders for **9 more**
tools in `app/documentary/detective/dispatch.py`'s `BUILDERS` registry, following the exact
`(db, action) -> dict | None` pattern `_fact_card_data`/`_floor_plan_data` already established.

## Shared helpers added

`_bound_claims`/`_bound_questions`/`_bound_relations(db, action)`: resolve a `DetectiveToolAction`'s
`content_bindings_json` entries (`{"claim_key": ...}` / `{"question_key": ...}` / `{"relation_key":
...}`) into real Section B rows, in binding order. `_ENGINE_BASIS`: the one honest mapping from
Section B's claim-basis vocabulary (`established_in_text`/`attributed`/`disputed`/`inference`/
`explicit_unknown`) to the cinematic engines' older, narrower vocabulary
(`fact`/`witness`/`disputed`/`finding`/`unknown`) — used by every engine builder below instead of a
per-tool guess.

## The 9 new builders (all render-tested against the REAL `Graphic`/engine renderer, not mocked)

- **`detective_board` (T02)**: one card per bound claim (`{id, title, fact_id}`).
- **`recap` (T03)**: `RecapBoard`'s three sections (confirmed/disputed/unknown), split strictly by
  claim `basis`. A claim whose text exceeds the card's real 38-char budget, or whose section is
  already at its real cap (3/2/2), is **skipped**, never truncated into something it didn't say.
- **`unknowns` (T04)**: `MapOfUnknowns` — bound claims (established/attributed) supply "known"
  points, bound questions supply "unknown" points; both are required (the engine's own rule).
- **`conflict` (T05)**: `ConflictPage` — from a bound `relation_type="conflict"` relation
  connecting exactly two claims; each side's `who`/`source` comes from the claim's own
  `attribution`, never invented.
- **`conflict_board` (T06)**: `ConflictBoard` — the board of 2+ bound conflict relations introduced
  so far, same rule as the pre-existing `alternatives.py:board_offer`.
- **`key_facts` (T10)**: `KeyFacts` — only a claim whose text *already* starts with a value short
  enough to fit the card's real 14-character limit is used; nothing is cut to fit.
- **`quote` (T12)**: `QuoteCard` — one `attributed` claim with a real `attribution` (speaker) under
  220 characters; never a bare `established_in_text` assertion (a quote card is specifically for
  someone's own words).
- **`evidence_check` (T15)**: `EvidenceCheck` — an entity's `established_in_text` claims become
  what the evidence "establishes"; its `disputed`/`explicit_unknown` claims become what it does
  "not establish"; capped at the engine's real `items_per_group=2`.
- **`custody` (T16)**: `ChainOfCustody` — 2-5 bound claims about one entity become its documented
  stages (`discovery`/`collection`/`documentation`/`transfer`/`storage`, the engine's real `STAGES`
  vocabulary, in binding order).

Every builder was checked against its renderer's actual `config/parameters/graphics.json` limits
(`item_max_chars`, `max_items`, `items_per_group`, `max_stages`, conflict `kinds`/`STATUSES`, etc.)
— several first drafts failed `can_render` until corrected (recap's real 38-char/3-2-2 caps,
conflict's real `kinds=["parties","witnesses","evidence"]`/60-char summary limit, evidence_check's
real `item`/`establishes`/`not_establishes`/`assessment` shape — not the issue/prosecution/defense
shape `courtroom` uses, which an earlier draft mistakenly copied). All 9 tests in
`tests/test_detective_dispatch_builders.py` call the real `Graphic.can_render`+`draw_state` (or
engine `.can_render`+`.draw`) — a passing test means a real frame was actually drawn, not just that
a dict looked plausible.

## Honestly deferred (8 tools remain `IN_PROGRESS`, with the specific schema gap stated)

- **`map`/zoom map (T01)**: needs a real place name + region feeding the `zoom_map` camera-curve
  subsystem — a materially different, larger integration than a data-shaping builder; Section B's
  entity schema has no structured place/region field yet.
- **`investigation_clock` (T08)**: the engine requires 2+ events each with a real `date` — Section
  B's `DetectiveClaim`/`DetectiveQuestion` carry no structured date field (only free text and a
  narration anchor). Building this honestly needs a date-extraction step this pass didn't attempt
  rather than a fabricated date.
- **`document` (T11)** and **`headline` (T13)**: both require verified real-world provenance
  (`document.id` against a stored official source and a verbatim-checked quote; a headline's `url`
  matching a listed newspaper) that only an existing `Fact`/`Source` row can authentically supply.
  `DetectiveClaim.evidence_fact_ids_json` is the designed link for this, but wiring it through to a
  genuinely verified document/headline (not an invented one) is real remaining work, not attempted
  this pass.
- **`forensic` (T17)**: needs a real `exam` type from a fixed vocabulary (`dna`/`fingerprint`/
  `toxicology`/etc.) — no equivalent field exists on a claim.
- **`interrogation` (T18)**: needs role-tagged (`question`/`answer`) verbatim/paraphrase turns —
  feasible in principle by pairing a `DetectiveQuestion` with an answering claim, but not built this
  pass to keep scope honest and complete rather than rushed.
- **`courtroom` (T19)** and **`sentencing` (T20)**: need issue/prosecution/defense/finding and
  charge/outcome structures respectively — no equivalent fields exist on a claim without further
  schema work.

## Checklist impact (first batch)

T02/T03/T04/T05/T06/T10/T12/T15/T16 → `VERIFIED` (new real render evidence). T01/T08/T11/T13/T17/
T18/T19/T20 stay `IN_PROGRESS` with the specific gap above. **D2 itself stays `IN_PROGRESS`**, per
the doc's own "all twenty tools have usable adapters" wording — 12 of 20 now have real dispatch
proof (T02/T03/T04/T05/T06/T07/T09/T10/T12/T14/T15/T16), 8 remain.

## Second batch — closing 5 of the remaining 8

Investigated each of the 8 remaining gaps against the actual renderer/schema code rather than the
earlier assessment alone:

- **`investigation_clock` (T08)**: closed. The claimed schema gap (no date on a `DetectiveClaim`)
  is bridged via the already-designed `evidence_fact_ids_json` optional link to a real `Fact` row —
  `Fact.event_date` supplies the date, parsed with `app.documentary.mvp.alternatives._event_date`
  (reused, not reimplemented). No date on the linked fact → the claim is skipped, never invented.
  The renderer's `track` field ("crime"/"investigation") has no claim-level equivalent either, so
  `DetectiveClaim.attribute` is used as that slot name — see the vocabulary note below.
- **`forensic` (T17)**: closed via the `attribute`-as-slot convention: a claim with
  `attribute="exam"` whose text is a real `EXAMS` member, `attribute="sample"`/`"method"`/`"result"`
  (all three required — an earlier draft treated "method" as optional per the renderer's `steps()`
  default list, but `can_render` requires it unconditionally; caught by the real renderer check, not
  assumed) and optional `attribute="limits"`. `method`'s basis is forced to `"expert"` since no
  Section B basis term maps to the renderer's `("illustration","expert")` allowance — an examiner's
  methodology is inherently expert-sourced content regardless of the underlying claim's basis.
- **`interrogation` (T18)**: closed. Each bound `DetectiveQuestion` becomes a "question" turn; a
  claim bound alongside it with `attribute="answer"` and the same `entity_node_key` becomes its
  "answer" turn. A question with no matching answer claim is included without one (an open
  question, not an error) rather than invented.
- **`courtroom` (T19)**: closed via `attribute` slots `"issue"`/`"prosecution"`/`"defense"`/
  `"finding"` (all required, matching the renderer's actual `can_render`).
- **`sentencing` (T20)**: closed. `attribute="charge"` claims become charges (required, >=1);
  `attribute="final"` is the closing sentence (**required** — an earlier draft treated it as
  optional, matching only the `steps()` list; the real renderer's last line,
  `kit.item_problem(data.get("final"), "the final sentence", ...)`, is unconditional, caught by the
  real-renderer test). `aggravating`/`mitigating` are optional one-item lists when a matching claim
  exists. `arrangement` (`{kind: concurrent|consecutive, fact_id}`) has a materially different shape
  from a text item and was deliberately left out rather than approximated.

**The `DetectiveClaim.attribute` vocabulary this introduces** (`exam`/`sample`/`method`/`result`/
`limits` for forensic; `issue`/`prosecution`/`defense`/`finding` for courtroom; `charge`/`final`/
`aggravating`/`mitigating` for sentencing; `crime`/`investigation` for the clock's track; `answer`
for interrogation) is a real-code convention, not yet reflected in the Detective Director's own
`schema_hints` (`director.py:_build_input`) the way the `already_introduced_subject_keys` hint
already is. Live model-produced content for these 5 tools would need that hint added before a real
agent call could reliably produce claims with the right `attribute` values — noted as follow-up
work, not done in this pass (no live call was needed for dispatch-layer proof itself).

**Still not closed, honestly** (now 3, not 8):
- **`map` (T01)**: not a schema/data problem after all — `MapPanel` is not a `Graphic`-registry
  renderer at all (confirmed: it has no `can_render`, isn't in `registry.py:GRAPHICS`; it's driven
  by a `SHOW_MAP` shot command built by `mvp/direct.py:add_map_cards` and drawn by
  `render/engine.py:_map_page`, a structurally different mechanism from the `GraphicCard`/
  `place_detective_graphics` carving path every other dispatch builder here uses). Wiring it needs
  the dispatch/carving layer itself extended to emit a `SHOW_MAP` shot, not a `_map_data` builder —
  a real, larger `BUILDERS`-pattern limitation, not a missing data field. (The place-name/region
  data itself IS available without new schema: `app.documentary.mvp.pipeline.case_region(case)` +
  the bound entity's `RevealNode.label`.)
- **`document` (T11) / `headline` (T13)**: unchanged from the first batch's assessment — real
  verbatim-against-official-source verification, reusing `alternatives.py`'s `document_offer`/
  `headline_offers` logic against `Evidence.official`/`Source` rows, was not attempted to avoid
  building a shortcut that weakens the existing verification bar.

## Checklist impact (second batch)

T08/T17/T18/T19/T20 → `VERIFIED` (5 new real render tests in
`tests/test_detective_dispatch_builders.py`). **D2 stays `IN_PROGRESS`**: 17 of 20 tools now have
real dispatch proof; T01/T11/T13 remain, each with a specific, non-trivial structural reason above
rather than an unattempted gap.

One pre-existing test, `tests/test_detective_section_d.py::test_unsupported_tool_id_is_reported_not_silently_dropped`,
used `"courtroom"` as its example of a tool_id with no builder at all — now stale since courtroom
has one. Updated it to use `"map"`, which genuinely still has none.
