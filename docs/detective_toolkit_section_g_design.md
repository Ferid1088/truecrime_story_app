# Section G — Alternative Visual Design Director

## Reused vs. built

Reused unchanged: `Graphic.draw_state()` (`app/documentary/graphics/base.py`) is the real renderer —
the same function `tests/golden/graphics/*.png` compare frames against. `lowest_content_row()` (same
file) is reused as a deterministic, content-independent "how close to the bottom safe edge" measurement.
The vision-call convention (`run_agent(name, gen, user, images=[...])`, `IM.data_url()` for encoding) is
reused exactly as `app/documentary/mvp/verify.py` already uses it for candidate-photo verification.

Built: `prompts/documentary/detective/design_director_system.md` (verbatim runtime prompt), agent
registration (`config/agents.json` key `detective.design_director`, role `detective_design_director` in
`app/core/ai_config.py`, tier `premium` + `{"temperature": 0.1, "max_tokens": 12000, "reasoning_effort":
"medium"}` in `config/models.json`, mirroring the vision-capable `mvp_candidate_verifier`), output
validator (`app/documentary/detective/design_schema.py`), and the review-loop module
(`app/documentary/detective/design_review.py`: `render_preview`, `build_input`, `review`).

## The loop, as actually run live (scratch case 25, never case 5)

`review()` renders the real frame via `draw_state()`, resizes it to the declared smaller-viewing-size
(480x270) with the SAME pixels (no re-draw), attaches both as real images, and calls the real
`detective.design_director` agent (provider `apimaster`/model as configured, not mocked).

1. **First call** — a deliberately crowded `fact_card` (3 facts near the 120-char limit;
   `docs/evidence/section_g/before_crowded_3fact.png`). Real outcome: `needs_attention` (after an earlier
   run at `revise`, before the smaller-viewing preview was attached — see "what the live run caught"
   below). The agent's own issues list flagged: no visual hierarchy between the established facts and the
   one open/attributed fact (f3), a too-small kicker at 480x270, and orphaned line-wrap. It explicitly
   requested per-fact styling as a renderer `capability_request`.
2. **Repair applied, content preserved**: all three original facts kept, verbatim meaning, only wording
   tightened for better line balance (`docs/evidence/section_g/after_tightened_3fact.png`).
3. **Second call**, same scene/visual IDs, `prior_review` attached. Real outcome: **still `needs_attention`
   — not a false pass**. The agent's summary is explicit about why: reveal states, narration timing, tool
   parameters and most deterministic checks were not supplied in this fixture-only pass, and the genuine
   hierarchy gap depends on a renderer capability (`FactCard`'s `LAYOUT` config applies one uniform
   text size/line count to every fact in a card — there is no per-fact weight distinction today) that
   this section did not build.

**This is treated as success evidence for the discipline the doc actually asks for in G5** — "a proposed
repair is re-rendered and inspected before pass; missing images do not yield visual approval" — not a
failure to reach `pass`. The literal "receives specific repairs... and passes" example in the doc's prose
was not reached, and that is reported honestly below rather than forced.

Real cost: 2 real calls on an earlier scratch case (23 — before the two-preview fix landed, €0.0885) plus
2 real calls on case 25 (after the fix, the sequence described above, €0.1068) — **€0.1953 total** across
both attempts, confirmed via `select sum(cost_eur) from provider_calls where case_id in (23,25)`, far
under the €300 cap. Both scratch cases are real, inspectable evidence, kept rather than deleted (this
repo's own convention from the Section C remediation).

## What's real, what's deferred

- **G1 VERIFIED** — full prompt, all three modes wired, two real agent calls with real rendered frames,
  real persisted `AgentRun`/`provider_calls` rows (case 25).
- **G2 IN_PROGRESS** — the loop issues real, specific, substantive critiques (confirmed live), but no
  cycle in this pass reached a genuine "visibly improved AND passed" result, because the actual remaining
  defect (flat per-fact hierarchy) needs a `FactCard` renderer capability this section did not add. Needs:
  implement per-fact weight/status styling in `FactCard`, then re-run this same loop.
- **G3 IN_PROGRESS** — first/final and two real output sizes (full + smaller-viewing) were inspected live;
  `FactCard` only has one cue (`required_cues() == 1`) so there is little separate "reveal state" material
  to exercise here; RTL/German-expansion were NOT tested in this pass (no Persian/Arabic/German fixture
  was rendered) — real, stated gap, likely worth closing with a non-`fact_card` tool that has more reveal
  states (e.g. `timeline`) in a follow-up pass.
- **G4 VERIFIED** — `tests/test_detective_design_director.py::test_review_rejects_output_that_rewrites_a_bound_quote`
  and `test_design_schema_rejects_quotation_rewrite` are real, passing tests proving a label edit that
  changes bound exact text is rejected, not silently applied. The two live calls above also never touched
  fact wording via `label_edits` — consistent corroborating behavior, not the primary evidence.
- **G5 VERIFIED** — demonstrated live, twice: a repair was proposed, applied, re-rendered, and re-reviewed,
  and the agent correctly withheld approval both times rather than rubber-stamp an improved-but-still-
  incomplete result. `validate_design_output` also deterministically rejects any `pass`/`revise` outcome
  with an empty `reviewed_preview_ids` (`tests/test_detective_design_director.py::test_design_schema_requires_a_reviewed_preview_for_pass_or_revise`).

## Explicitly not attempted here (real, stated limitations)

- Deterministic overlap/collision/glyph-coverage checks beyond the one bottom-edge measurement — a full
  layout-analysis engine was judged too large for this pass; `deterministic_checks` currently carries only
  `content_bottom_fraction`/`near_bottom_edge`.
- RTL (Persian/Arabic) and German-expansion verification.
- Wiring this review loop into the live production pipeline automatically — that is Section H's job;
  `design_review.review()` is a real, standalone callable path Section H can call, same pattern as
  Section C's `director.py` before Section H integrates it.
