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

## Checklist impact

T02/T03/T04/T05/T06/T10/T12/T15/T16 → `VERIFIED` (new real render evidence). T01/T08/T11/T13/T17/
T18/T19/T20 stay `IN_PROGRESS` with the specific gap above. **D2 itself stays `IN_PROGRESS`**, per
the doc's own "all twenty tools have usable adapters" wording — 12 of 20 now have real dispatch
proof (T02/T03/T04/T05/T06/T07/T09/T10/T12/T14/T15/T16), 8 remain.
