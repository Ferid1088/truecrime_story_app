{persona_prompt}

---

# Your task now: the host plan for this episode (steps 1–5 of the Procedure)

You receive the complete verified narration beat by beat (the editorial
blueprint: purpose, reveals, listener questions, emotional load), the
evidence list (F… facts, T… timeline, C… contradictions), the ARCHIVE of
previously covered cases (refs A…), HOST MEMORY (refs M…) and the host's
RECENT SEGMENTS from earlier episodes. Do not write dialogue yet; decide
where the host appears and what each appearance must do. The dialogue is
written later, natively in every language, from your plan.

Placement:
- position "opening" ({sec_opening}): before the first beat. At most one.
- position "mid" ({sec_mid}): after a beat ("beat_id"). At most
  {max_mid_segments}, usually one or two, only at a meaningful moment.
  Never right after a hook beat, never right before a reveal beat (the
  payoff belongs to the narration), at least {min_beats_between} beats
  between two appearances.
- position "final" ({sec_final}): after the last beat, optional.
- Fewer is better than forced. An appearance that only repeats the
  narration is worse than none.
- All appearances together: at most {max_total_seconds} seconds in the
  whole film (the sum of every target_seconds).

Reveal firewall: at its placement the host knows only what the viewer has
heard so far. Never use evidence a LATER beat reveals (the opening may
only tease what the first beat reveals).

Memory: "memory_reference" is the ref (A… or M…) of a GENUINE, specific
connection, or null. No ref, no memory — never invent one; a weak
similarity is left out.

Claims: list every factual statement the host will make with its kind
({claim_kinds}) and the evidence ids that support it.
Speculation and personal reactions are allowed only labelled as such.

Variety: look at the recent segments' patterns and dimensions and choose
differently (another opening pattern, other dimensions).

memory_updates: what the host will remember about THIS case for later
episodes, in English — opinions taken, reactions, corrections of an
earlier reading, questions left open, recurring themes ({memory_kinds}).
Only what the plan actually expresses and the evidence supports; 0–6 items.

Return JSON only:
{{"notes": "one or two sentences on the host's role in this episode",
  "segments": [{{"id": "S1", "position": "opening", "beat_id": null,
    "pattern": "a detail to remember | competing accounts | ... (short label)",
    "purpose": "why the host appears here",
    "dimensions": ["curiosity"],
    "memory_reference": null,
    "memory_connection": null,
    "delivery": "direct and conversational; slightly faster than narration; ...",
    "intent": "what the host says and does here, in English notes (not dialogue)",
    "claims": [{{"text": "...", "kind": "confirmed_fact", "evidence_ids": ["F003"]}}],
    "target_seconds": 25,
    "transition_back": "how the segment hands back to the narration"}}],
  "memory_updates": [{{"kind": "open_question", "text": "...", "segment_id": "S1"}}]}}
Dimensions come from: {personality_dimensions}.
