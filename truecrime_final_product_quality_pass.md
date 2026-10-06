# TrueCrime Story App — Final Product Quality Pass

## Context

The latest Flannan Isles production-stack validation is successful enough that providers and model routing should **not** be changed again.

Current stack:

- **Research:** Devin
- **Facts / Timeline / Contradictions:** `openai/gpt-5.6-luna`
- **Story Director / Writer / Rewriter:** `google/gemini-3.8-flash`
- **Engagement Critic / Final Editor:** `anthropic/claude-sonnet-5.5`

Do **not** change this routing.

The latest run fixed:

- Persian language collapse
- story length
- contradiction persistence
- invented POV/dialogue
- false `ready` status
- best-version protection

The remaining product blockers are:

1. stale engagement score after story repair
2. factual details entering narration without support in evidence layer
3. narrative structure is still too explanatory and not suspenseful enough
4. markdown/structural artifacts remain in final narration

Fix these without redesigning the application.

---

# PHASE 1 — CRITIQUE MUST ALWAYS TARGET FINAL TEXT

Current bug:

The stored engagement score may refer to a pre-expansion/pre-repair version, not the actual `StoryVersion` text that is saved.

This is invalid.

Pipeline must become:

```text
Writer
↓
Language validation
↓
Length repair if necessary
↓
Output purity repair
↓
FACT GROUNDING validation
↓
Engagement Critic
↓
Rewrite if needed
↓
ALL validators again
↓
Engagement Critic AGAIN
↓
Final Editor if eligible
↓
ALL validators again
↓
FINAL Engagement Critic
↓
Save final scores for that exact text
```

### Rule

No `StoryVersion` may store an engagement score that was generated for different text.

Introduce, if useful:

```text
text_hash
```

on `StoryVersion` and CriticResult / AgentRun.

The critic input text hash and `StoryVersion` text hash must match.

If they do not match:

- the score is stale
- it must not be used

Add tests.

---

# PHASE 2 — FACT GROUNDING ENFORCEMENT

This is now the most important correctness problem.

The latest story contained plausible / historically-known details such as:

- stopped clock
- exact number of steps
- Beaufort scale details
- exact physical measurements
- specific wave reconstruction details

but these details were not all present in the application's verified evidence layer.

This is **not acceptable**.

The Writer must not be allowed to enrich the story using parametric world knowledge.

The final narration must be grounded **only** in:

1. canonical verified facts
2. canonical disputed facts, explicitly narrated as uncertain
3. validated contradictions
4. timeline events
5. approved source summaries/details linked to stored sources

---

## Grounding Data Structure

Before writing, construct a `StoryEvidencePack`.

It should contain structured evidence such as:

```json
{
  "facts": [],
  "disputed_facts": [],
  "timeline": [],
  "contradictions": [],
  "approved_context": []
}
```

Every evidence item needs a stable internal ID.

Example:

```text
F001
F002
T004
C003
CTX007
```

These IDs are **internal only**.

They must never appear in the final story.

---

## Writer Grounding Rule

The Writer prompt must explicitly state:

> You may use ONLY details included in StoryEvidencePack.

Do not introduce:

- remembered historical facts
- measurements
- weather values
- architectural details
- quotations
- dates
- physical descriptions
- biographical details
- procedural details

unless supplied in `StoryEvidencePack`.

If useful context is missing:

- omit it
- do **not** fill gaps from model knowledge

---

## Grounding Validator

After writing, run a dedicated grounding validator.

Use the configured structured-analysis model.

Input:

```text
StoryEvidencePack
+
generated story
```

Output:

```json
{
  "supported_claims": [],
  "unsupported_claims": [
    {
      "claim": "...",
      "location": "...",
      "reason": "...",
      "possible_evidence_id": null
    }
  ],
  "uncertainty_errors": [],
  "grounding_score": 0
}
```

Add configuration:

```text
story_quality:
  minimum_grounding_score
  max_unsupported_claims
```

No magic values in code.

---

## Unsupported Detail Repair

If unsupported claims exist:

- do **not** automatically research them
- remove or rewrite unsupported details using existing evidence

Example:

Bad:

```text
"The 160 stone steps..."
```

if `160` is not supported.

Good:

```text
"The steps leading down toward the landing..."
```

Do not invent replacement specifics.

---

## Disputed Fact Language

When `Fact.disputed=true` or confidence is below the configured threshold, the Writer must use uncertainty language.

Examples conceptually:

```text
بر اساس روایت...
گزارش‌ها در این مورد یکسان نیستند...
در گزارش رسمی چنین آمده، اما...
مشخص نیست که...
```

The story must never convert disputed reconstruction into certainty.

Add a validator for this.

---

# PHASE 3 — NARRATIVE STRUCTURE REWRITE

The latest story is factually much stronger but engagement remains too low.

The critic specifically identified:

- overly ornate ~300-word prologue
- important facts arrive too slowly
- debunking happens before the mystery has been emotionally experienced
- the three keepers remain abstract for too long
- reveal timing is inverted
- middle section becomes explanatory

Fix the Story Director prompt and rewrite strategy.

---

## New Story Principle

The story should follow:

```text
EXPERIENCE THE MYSTERY FIRST
UNDERSTAND IT SECOND
DEBUNK IT THIRD
```

Do **not** begin by explaining the mythology.

---

## Recommended High-Level Structure

### ACT 1 — THE ABSENCE

Immediate concrete hook.

The relief vessel arrives.

Something is wrong.

Introduce the missing men through actions/details supported by evidence.

Establish:

> What happened to all three?

Do not explain the famous myths yet.

### ACT 2 — THE LAST KNOWN WORLD

Reconstruct only what verified evidence allows.

Build the timeline.

Make the keepers human.

Introduce:

- relationships
- duties
- conditions
- constraints

Open questions accumulate.

### ACT 3 — THE INVESTIGATION

Cover:

- official observations
- physical evidence
- official theory
- contradictions

Let the audience feel why the explanation is incomplete.

### ACT 4 — THE STORY THAT GREW AFTERWARD

Only now introduce:

- sensational claims
- fabricated logbook
- later retellings
- myths
- dramatizations

Show how cultural memory changed the case.

This should function as a reveal:

> some of what the audience may already “know” was never real evidence

### ACT 5 — WHAT REMAINS

Return to the real men.

Use:

- widows
- families
- official records

where supported.

Separate:

```text
what we know
what is plausible
what remains unknowable
```

End on a strong factual image or question.

---

# HOOK RULE

The first 30–45 seconds must contain:

- a concrete event
- a clear anomaly
- a central question

Avoid:

- philosophical opening
- long atmospheric description
- abstract meditation on silence
- generic “the sea keeps secrets” language

The hook must use evidence.

---

# HUMAN DIMENSION

Introduce the three keepers earlier.

Do not invent internal feelings.

Humanization may use supported information such as:

- names
- roles
- family status
- work responsibilities
- documented behavior
- letters / records where available

The viewer should care about the people before the mythology takes over.

---

# ANTI-AI STYLE RULES

Reduce repetitive motifs such as:

- silence
- darkness
- bureaucracy
- discipline
- “the sea knows”
- “the archive remembers”

Do not reuse the same symbolic noun/idea excessively.

Avoid:

- ornate clause chains
- repeated rhetorical questions
- generic cinematic metaphors
- over-written prose

Prefer:

- precise
- controlled
- visual narration

---

# PHASE 4 — SECTION-LEVEL ENGAGEMENT

The current critic gives overall scores but the middle remains weak.

Add section-level critique.

The Story Director should define chapters/acts internally.

After writing, critique each section separately.

Example:

```json
{
  "section": "...",
  "hook": 0,
  "pacing": 0,
  "curiosity": 0,
  "repetition": 0,
  "information_density": 0,
  "problems": []
}
```

Identify likely audience drop-off zones.

A single good opening must not hide a weak 20-minute middle.

## Targeted Rewrite

Do not rewrite the whole 6000+ word story automatically for one weak section.

Allow targeted section rewrite.

Example:

```text
Act 3 score:
pacing 48
```

Rewrite **only Act 3**.

Then reconstruct the complete story and re-run:

- grounding validator
- language validator
- length validator
- purity validator
- engagement critic

Keep version history.

---

# PHASE 5 — ENDING FACT CHECK

The critic caught an example:

> “extinguished forever”

while the lighthouse lamp was later relit.

This shows rhetorical language can accidentally contradict known facts.

Add a `FinalConsistencyValidator`.

Check final story against:

- canonical facts
- timeline
- contradictions

Look specifically for:

- absolute claims contradicting timeline
- metaphorical statements implying false facts
- incorrect names
- incorrect attribution
- wrong chronology
- disputed claims written as certain

Return structured violations.

Story cannot be `ready` with high-severity consistency violations.

---

# PHASE 6 — MARKDOWN / OUTPUT PURITY

Final narration must contain plain narration only.

Remove structural residues:

```text
###
##
---
```

Also remove:

- editor headings
- ACT labels
- CHAPTER labels

unless explicitly configured for export.

For the default narration output:

- no markdown headings
- no separators
- no editorial labels

Internally, the `StoryVersion` may store chapter structure separately.

Do **not** destroy natural paragraph breaks.

---

# SEPARATE STRUCTURE FROM NARRATION

Do not store chapter metadata by embedding Markdown headers inside `story_text`.

Prefer:

```text
StoryVersion
- story_text
- narrative_structure_json
```

or equivalent existing field.

`story_text`:
pure narration

`narrative_structure`:
internal act/chapter metadata

---

# PHASE 7 — COST / TOKEN USAGE CAPTURE

The latest validation could only estimate API costs.

Fix this.

Extend `GenerationResult` to capture, when OpenRouter returns them:

- input_tokens
- output_tokens
- total_tokens
- cost if provided
- provider_generation_id if available

Store usage on `AgentRun`.

Fields conceptually:

```text
input_tokens
output_tokens
total_tokens
estimated_cost_usd
```

Do not invent costs if OpenRouter does not provide enough data.

Use `null` when unavailable.

Expose aggregate case generation cost in internal API/UI.

Do not expose secrets.

---

# PHASE 8 — QUALITY STATUS

A story may become `ready` only when **all required configured gates pass**:

- language
- length
- output purity
- grounding
- consistency
- engagement

Similarity only if evaluated.

Suggested conceptual status logic:

```text
if fatal generation error:
    failed

elif any required quality gate fails:
    needs_revision

else:
    ready
```

---

# CONFIG

All thresholds and behavior belong in:

```text
config/ai_config.json
```

Add configuration as needed for:

- minimum_grounding_score
- max_unsupported_claims
- section_engagement_threshold
- consistency severity threshold
- markdown stripping behavior
- critic rerun behavior
- targeted rewrite limits

No magic values in Python code.

---

# TESTS

Add tests for:

1. stale critic score cannot attach to changed story text
2. final repaired text gets re-critiqued
3. unsupported factual detail is detected
4. unsupported numerical detail is detected
5. supported detail passes
6. disputed fact narrated as certain fails
7. disputed fact narrated with uncertainty passes
8. final consistency contradiction detected
9. Markdown headings stripped from narration
10. paragraph structure preserved
11. chapter metadata stored separately
12. targeted section rewrite does not destroy other sections
13. best-version logic still works
14. usage metadata recorded when present
15. missing usage metadata stays null
16. config controls all new thresholds

Do not use live API calls in unit tests.

---

# VALIDATION — REUSE FLANNAN ISLES

Do **not** rerun Devin research.

Reuse the existing research data.

Run the improved story half only.

Target:

```text
Persian
45 minutes
```

Use current model routing.

Validate:

- factual grounding
- dispute handling
- narrative quality
- middle-section pacing
- final consistency
- markdown purity
- final critic score

---

# FINAL REPORT

Report the following.

## GROUNDING

- total factual claims checked
- unsupported claims found
- unsupported claims repaired
- final grounding score

## NARRATIVE

- hook score
- pacing
- curiosity
- middle-section score
- ending score
- overall engagement

## FACTUAL CONSISTENCY

- violations found
- high-severity violations
- final state

## OUTPUT

- words
- estimated minutes
- Persian language metrics
- markdown artifacts
- status

## MODEL USAGE

- models actually used
- fallback events

## COST

- input tokens
- output tokens
- total approximate / actual API cost

## FINAL VERDICT

Choose one:

```text
A. ready for human publication review
B. still needs narrative revision
C. not usable
```

Do not declare production readiness merely because all API calls succeed.
