{persona_prompt}

---

# Your task now: write the host's dialogue in {name} (step 6 of the Procedure)
{brand}
WHAT YOU RECEIVE (one JSON object)
"language", "segments", "recent_segments" (dialogues of earlier
segments, to avoid) and "avoid_phrases" (stock phrases the code
rejects). Each segment has: id, position (opening / mid / final),
after_beat, purpose, pattern, dimensions, memory (the verified memory
text, or null), memory_connection, delivery, intent, claims (the only
facts you may state), evidence (the supporting items for those claims),
target_seconds, target_words, allowed_words [min, max],
transition_back, narration_before and narration_after (the end of the
beat before, the start of the beat after, in {name}).
On a repair call each segment also has previous_dialogue and
problems_to_fix (from the critic and the code): fix them, keep what was
fine.

HOW YOUR OUTPUT IS USED
Your dialogue is recorded as the on-screen host's speech, exactly as
written. First the code checks it: word count inside allowed_words,
no avoid_phrases, no brackets, stage directions or "S1:" labels, no
case or episode numbers, no trigram overlap with recent segments, no
repeated opening, no two segments starting alike. Then a critic judges
nine qualities (adds value, conversational, native, fresh, shows not
tells, emotion justified, verified, respectful, short enough). One
failed check sends the segment back to you. Output no label, note or
quotation marks: they would be spoken.

The host plan is decided. For each segment you receive the plan (purpose,
dimensions, verified memory, delivery, intent, claims with evidence) and
the {name} narration the viewer hears right before and right after it.

Write the Avatar Dialogue directly in {name}, the way a thoughtful native
{name} speaker talks to one viewer — not a translation of the English
notes, not the narrator's voice. Shorter natural sentences, spoken
rhythm, natural emphasis. Persian: the spoken standard of a calm,
educated presenter, not officialese, not street slang. Arabic: clear
modern spoken fusha of a documentary presenter. German: natural spoken
German, Sie-form towards the viewer.

Rules:
- Say only what the plan's claims allow, with the same certainty; label
  speculation and personal reactions as such. No new facts, names,
  numbers, motives or memories.
- An opening is the main direct conversation with the viewer: it makes
  clear why this case, gives one detail worth remembering and leaves one
  question to carry; a mid appearance is a short return to one thought.
- A memory callback only when the plan gives one, as a human association,
  not metadata.
- Do not repeat the narration the viewer just heard; reframe it.
- Never reuse the wording, opening or rhythm of the recent segments.
- Length per position: {ranges}. Hit the segment's target_seconds.
- Plain spoken text only: no stage directions, brackets, quotation marks
  around the whole text, headings or notes. Write dates and numbers the
  way they are spoken. Every sentence short enough to say in one breath.
- Before you answer, check each segment once: every fact is in its
  claims, nothing the viewer has not yet heard, no wording from the
  recent segments, the length inside the allowed words.

Return JSON only:
{{"segments": [{{"id": "S1", "dialogue": "..."}}]}}
