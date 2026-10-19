{persona_prompt}

---

# Your task now: plan WHERE the host appears in this film (once, for all languages)

You decide the STRUCTURE of the host's appearances and nothing else. You do not write what the
host says, you do not name facts, evidence, motives or memories, and you do not choose seconds:
the code computes the seconds of each appearance from its role, the weight of the act before it
and its place in the film, and it computes what the host may know at that point (every act
before it, nothing after). You see the film's acts so that you can find the right moments.

WHAT YOU RECEIVE (one JSON object)
- case, film_seconds: the narration's length without the host.
- acts: in film order; each has id, number, title, purpose, words, seconds, opens_questions,
  answers_questions, is_hook (the act that opens the film) and is_payoff (an act that answers
  the story's questions: the host never stands right before it).
- On a repair call you also get "previous_plan" and "errors_to_fix": the code's errors for your
  last plan. Fix every one, keep the rest.

ROLES (the kinds of appearance; the allowed seconds are computed, shown here for orientation)
{roles}
- "opening": before the story, the hook. Exactly one. It comes first; the channel's logo
  follows it, then the story.
- "transition": a short bridge after an act, at a natural tension point.
- "reflection": the host stays with one thought a little longer after an important act.
- "viewer_question": the host puts one question to the viewer to carry into the next act.
- "outro": after the last act, short. Exactly one.

HOW MANY, WHERE
- The number follows the structure and the film's length, not a fixed pattern: between
  {min_appearances} and {max_appearances} appearances in total, the opening and the outro
  included. A short film or an even, fast story needs fewer; a long film with several turning
  points more.
- A mid appearance ("transition", "reflection", "viewer_question") stands AFTER an act
  ("after_act": an act id), at an act boundary or natural tension point, never in the middle of
  a scene. Never after the hook act, never right before a payoff act, never after the last act
  (that is the outro).
- At least {min_acts_between} act between two appearances and at least {min_gap_seconds} seconds
  of story between them. Vary the roles; do not stack the same role twice in a row.
- All appearances together stay under {max_total_seconds} seconds and under {max_total_share}. Fewer is better than forced.

Return JSON only:
{{"notes": "one or two sentences on why this structure fits this film",
  "segments": [
    {{"id": "S1", "position": "opening", "role": "opening", "after_act": null,
      "reason": "why the host stands here (structure only: tension, turn, pacing)"}},
    {{"id": "S2", "position": "mid", "role": "transition", "after_act": "act2",
      "reason": "..."}},
    {{"id": "S3", "position": "final", "role": "outro", "after_act": null, "reason": "..."}}]}}
"position" is "opening", "mid" or "final" and must match the role's position.

# ADDED: planning rules
You plan WHERE and HOW LONG the host speaks. You output structure only: for each appearance an id, the act it follows, a role (opening, transition, reflection, viewer_question, outro), target seconds and the cutoff act. You never write or hint at content. The opening comes first, before the logo video and the story; the outro closes the film. Place the other appearances at act boundaries or at natural tension points, never inside a scene and never right before a reveal. Do not use equal durations: opening and outro are longer, transitions short; weigh each appearance by the act before it and its position in the film. Respect the configured minimum gap, maximum number of appearances and maximum host share of the runtime.
