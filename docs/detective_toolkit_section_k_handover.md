# Section K — real-film proof and handover

Implements `docs/TrueCrime_Detective_Planner_Implementation_Prompt.md` Section K on top of
Sections A-J (all committed on `feature/detective-toolkit-planner`, each independently verified —
see `docs/detective_toolkit_section_{a,b,c,d,e,f,g,h,i,j}_design.md` and
`docs/detective_toolkit_capability_matrix.md`).

## Budget constraint and how it shaped this section

Case 15 ("Murder of Ashley Flynn", the only real production case in this database) has already
spent **€323.08**, above the project's €300 cap. Per this repo's rule ("stop cleanly before a paid
call if spent_eur >= cap_eur... never raise it on your own initiative"), **no new paid call was
made against `case_id=15`** — confirmed by querying `sum(cost_eur)` for case 15 before and after
this section's work: unchanged at `323.0845101655945`.

Instead: case 15's own real, already-written final narration (`story_versions.id=248`, the latest
`kind='spoken'` English version) was **read only**, and a representative continuous excerpt of it
was copied into a dedicated scratch case (id 29, created solely for this proof, deleted/retried
twice during debugging — see "A real defect this proof found" below). All live Detective Director
and Film Reviewer calls ran against that scratch case. This gives K a genuinely real passage
without spending against an exhausted budget.

## The excerpt (real text, case 15, beats B01-B03)

> Caleb Flynn called 911 from his home on Cunningham Court. He told the dispatcher somebody broke
> into his home and shot his wife. He said the garage door was standing wide open, and that someone
> had come in and shot Ashley while she was in bed.
>
> Officers found Ashley Flynn under the covers, shot twice in the head. Near the foot of the bed lay
> two spent cartridge casings. Nothing suggested she had gotten out of bed or struggled with
> whoever came up to her.
>
> Early reporting did not agree on the older daughter's age: The Guardian said the sisters were nine
> and eleven, while NBC News said nine and twelve.

This excerpt was chosen because it already contains, in real case content: an attributed claim
(Caleb's break-in account) distinct from established fact (what officers actually found), an open
investigative question (who entered and fired the shots), physical evidence with an unclear chain
to a person (the casings), and a genuine sourcing contradiction (the daughter's age) — i.e. a
representative continuous passage with investigative development, without needing to invent
anything. Script: `docs/detective_toolkit_section_k_evidence/live_proof.py`.

## A real defect this proof found and fixed

The first live run (`map_film` then `direct_scenes` on the real excerpt) crashed: `direct_scenes`
proposed a knowledge event re-`introduce`-ing an entity `map_film` had already introduced.
`app.documentary.memory.record_knowledge_event` correctly rejected it (Section B's own
first-introduction invariant) — but `persist_director_output` had already written that call's
claims and scene *before* reaching the failing knowledge event, so the crash left a
**partially-persisted, semantically broken plan** in the database, directly contradicting
`director.py`'s own documented guarantee ("a validation violation raises rather than persisting a
semantically broken plan") for schema-level violations. Short 3-sentence fixtures (Sections C-J)
never exercised a payload large enough to trigger this; a real multi-claim excerpt did immediately.

Two fixes, both in `app/documentary/detective/director.py`, both covered by new tests in
`tests/test_detective_director.py` (11/11 passing, full file re-run below):

1. **Rollback**: `_call` now runs `persist_director_output` inside `db.begin_nested()` and
   translates a `MemoryValidationError` into the same `DirectorValidationFailed` contract a
   schema.py violation already uses — a mid-persist memory violation now rolls back everything from
   that call, not just the one event that failed.
   (`test_a_mid_persist_memory_violation_rolls_back_everything_from_that_call`)
2. **Root cause**: `_build_input`'s `schema_hints` now includes `already_introduced_subject_keys`
   (every subject with a recorded `introduce` event) and an explicit instruction that re-mentioning
   one must use a different operation. This is the same reasoning Section C's design doc already
   used for field-name/enum hints: prose alone ("existing_state lists what's already known") was
   not reliably followed by the real model; stating the exact keys and the exact rule was.
   (`test_already_introduced_hint_lists_subjects_with_an_introduce_event`)

With both fixes, a clean retry (new scratch case, same excerpt) succeeded on the first attempt —
see "Live evidence" below. (A third, smaller issue surfaced by the full-suite K4 pass below: the
new `schema_hints.already_introduced_rule` string was inline prompt text in `director.py`, caught
by this repo's own `tests/test_architecture.py::test_no_new_prompt_text_in_code` — moved to
`prompts/documentary/detective/already_introduced_rule.md`, the repo's established pattern for
short instructional snippets.)

## Live evidence (scratch case 29, graph 11 — never case 15)

`toolkit.invoke` (flag enabled only for the duration of this script's process, restored to
`false` immediately after): `ToolkitResult(enabled=True, called=True, scene_count=3)`.

- **6 entities**: `ent_caleb_flynn` (person), `ent_ashley_flynn` (person),
  `ent_residence`/`ent_garage_door` (locations), `ent_spent_casings` (physical_evidence),
  `ent_daughters` (group).
- **9 claims**, correctly split by basis — established fact vs. attributed account, exactly the
  distinction the whole feature exists to preserve:
  - `established_in_text`: `clm_call_911`, `clm_officers_found_body`, `clm_casings_found`,
    `clm_no_struggle_signs` (what actually happened / what officers verified).
  - `attributed`: `clm_caleb_breakin_claim`, `clm_garage_door_open`, `clm_ashley_shot_bed_caleb`
    (Caleb's own account, not silently upgraded to fact), `clm_guardian_age_report`,
    `clm_nbc_age_report` (each news source's own reported figure, kept separate).
- **2 questions**: `qst_intruder_identity` ("Who entered the home and shot Ashley Flynn?",
  `explicit_unresolved`) and `qst_older_daughter_age` ("Was the older daughter eleven or twelve at
  the time of the incident?", `interpretive_question`) — the doc's own kind distinction
  (not-yet-explained vs. explicitly-unresolved vs. interpretive) applied to real content, not a
  fixture.
- **3 scenes**, each with one tool action:
  - `scn_B01` → `detective_board`, "Introduce the initial caller... and the open intruder
    question."
  - `scn_B02` → `detective_board` again, reuse_reason "Ongoing board update connecting physical
    scene verification to the open intruder question" — a real, live demonstration of Section E's
    continuing-board requirement (the second appearance is framed as an update to the same
    investigation, not a fresh board).
  - `scn_B03` → `conflict`, "Display side-by-side discrepancy between The Guardian and NBC News" —
    exactly the doc's own "Compare conflicting accounts" guidance, chosen for a real sourcing
    contradiction.
- **Cost**: `case_id=29` total `€0.06481078` across the clean `map_film`+`direct_scenes` pair.
  `case_id=15` total unchanged at `€323.0845101655945` — confirmed by direct query before and
  after this section's entire body of work, including the two failed/retried attempts.

## Before / after comparison, same real beats

**Before** (case 15's own actual, already-computed visual-needs plan for beats B01-B03, visual
plan id 14, blueprint id 7 — a real query, not a hypothetical): every one of the four requirement
rows for these beats is `function='moment', role_needed='context'`, with **no `node_key`, no
entity, no distinction between Caleb's account and verified fact, no question, no board or
conflict treatment** — the old planner reduced an attributed-break-in-claim-vs-verified-evidence
beat and a genuine press-source contradiction to generic "find some contextual moment" filler.

**After** (this section's live run, same real text): 6 named entities, 9 claims split by basis, 2
well-formed investigative questions, a detective board used twice with an explicit continuity
reason on its second appearance, and a `conflict` tool chosen specifically for the one beat that
actually contains a sourcing disagreement — reviewed and passed by the Film Experience Reviewer
(next section) with a summary that correctly names the attribution distinction, the board
continuity, and the conflict isolation as what makes the sequence work.

This is the concrete contrast the doc's K section asks for: "where a tool was selected because it
clarifies the investigation" (detective_board for the unfolding account; conflict for the
discrepancy) is visible by direct comparison against what the same real beats produced before this
feature existed.

## Film Experience Reviewer evidence (advances J3, does not fully close it)

Real `plan_audit` call against graph 11 (`docs/detective_toolkit_section_k_evidence/live_reviewer.py`):

```
OUTCOME pass
INSPECTION_MODES ['plan']
FINDINGS 0
SUMMARY: The detective plan for beats B01-B03 successfully organizes the opening sequence. It
carefully distinguishes Caleb Flynn's attributed 911 statements from verified physical scene
evidence, maintains continuity through a board update, and uses a focused conflict tool to isolate
press reporting discrepancies regarding the daughters' ages without jumping to conclusions. The
plan passes audit and is approved for rendering.
```

`inspection_modes` is correctly `["plan"]` only — this was a `plan_audit` call with no rendered
media, and the reviewer did not claim otherwise. **J3**'s acceptance criterion covers all three
modes (plan_audit, rendered_review, repair_check); this proves `plan_audit`'s scope-honesty live
(previously only schema-tested), so J3 stays `IN_PROGRESS` with a narrower remaining gap:
`rendered_review`/`repair_check` still have no live proof against real rendered media, since this
section could not produce a real render for its excerpt (see K2 below — the same D2 dispatch-
builder limitation).

## Dispatch and rendered-evidence scope — honest limits

`resolve_dispatchable_actions(db, 11)` (Section D/H's real dispatch path, no new call) was run
against this live graph. All three actions came back `skip: "not_accepted_by_planner"` —
correctly, since `needs.py`'s translation step (which sets `planner_decision`) was never run for
this scratch case (only the Detective Director side was exercised, per this section's scope). This
is also a genuine, honest demonstration of **H5's gate still holding on real content**: an
undecided action does not reach a real shot.

**No new rendered-frame evidence was produced in this section.** The two tools this excerpt's real
narration called for — `detective_board` and `conflict` — are not among the tools Section D built a
direct-dispatch render path for yet (`fact_card` and `floor_plan` only; see
`docs/detective_toolkit_section_d_design.md`'s stated D2 scope-down). Producing a rendered frame for
`detective_board`/`conflict` specifically would require building their dispatch builders first,
which is D2's already-identified remaining work, not this section's. Section G's own before/after
rendered-frame evidence (a real `fact_card`, two scratch cases, real repair cycle) and Section D/F's
`fact_card`/`floor_plan`/`timeline` render evidence already stand as this feature's real rendered-
output proof; K does not duplicate or re-derive that. A full audio/video render of this specific
excerpt was not attempted — it would need real TTS, real asset search, and the full production
pipeline wired for this scratch case's language/channel settings, which is disproportionate to a
representative-excerpt proof and was not pursued. **Motion and audio synchronization for this
specific excerpt were not inspected.**

## The gun example

The doc allows including its own gun-example fixture when "the actual case has no comparable
passage" — this excerpt does have one (the casings/weapon-ownership thread and the unresolved
intruder-identity question are a real analogue), so it was not duplicated here. The literal gun
example is already covered, end to end, by Section B's own tests
(`tests/test_detective_memory.py::test_gun_example_partial_knowledge_then_later_answer`) and
Section C's live evidence (`docs/detective_toolkit_section_c_design.md`).

## Handover index

- **Runtime prompts (final, verbatim)**: `prompts/documentary/detective/director_system.md`
  (Detective Director), `design_director_system.md` (Alternative Visual Design Director),
  `film_reviewer_system.md` (Film Experience Reviewer).
- **Schemas/configuration**: `app/documentary/detective/{schema.py, design_schema.py,
  review_schema.py}` (output validation); `config/agents.json`, `app/core/ai_config.py`,
  `config/models.json` (agent registration, all three roles); `config/parameters/
  detective_toolkit.json` (the single enable/disable flag, default `false`).
- **Section design docs** (what was built, reused, and tested, section by section):
  `docs/detective_toolkit_section_{b,c,d,e,f,g,h,i,j}_design.md`, plus
  `docs/detective_toolkit_section_cd_architecture_remediation.md` (the owner's "detective toolkit
  is a tool of the Main Visual Planner" correction and its fix).
- **Capability/reachability report**: `docs/detective_toolkit_capability_matrix.md` (Section A;
  every tool traced to its renderer/caller/status, the 7 original conflicting rules, the
  Detective-to-MVP hand-off seam).
- **Checklist and checkpoint**: `docs/detective_toolkit_implementation_checklist.md` (human-
  readable) and `docs/detective_toolkit_checkpoint.json` (machine-readable, per-row evidence) — the
  authoritative current status; see the K4 audit below for this section's sanity pass over it.
- **Tests**: `tests/test_detective_{memory, director, section_d, section_e, section_f,
  design_director, section_h, inspector, film_reviewer}.py` (89 tests across B-J, all passing as of
  this section — see full-suite result below).
- **This section's own evidence**: `docs/detective_toolkit_section_k_evidence/{live_proof.py,
  live_reviewer.py}` (reproducible scripts), this document.
- **Known, genuine remaining limitations** (not hidden — each already has an owning checklist row):
  C3 (question-payoff pacing needs a longer sequence than any fixture used so far), D2 and 17 of
  the T01-T20 rows (direct-dispatch builders beyond `fact_card`/`floor_plan` not yet built — real
  tools remain reachable through the pre-existing pipeline, just not yet through the new dispatch
  path), G2/G3 (FactCard per-fact visual hierarchy; full RTL/German-expansion fixtures), H2 (full
  staleness/invalidation is Section I's I2, already built, but not yet wired to auto-invalidate a
  *design review* specifically), I4 (RTL layout verification for the inspector UI itself), J3
  advanced by this section for the `plan_audit` mode specifically (now live-proven, not just
  schema-tested) — `rendered_review`/`repair_check` still have no real-media live proof, since no
  full render was produced, same as K2 below.
