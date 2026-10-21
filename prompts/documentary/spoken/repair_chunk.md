
You are now revising ONLY the beats given. This message replaces the
earlier INPUT format: the JSON has "beats", each with "beat_id",
"source" (the fact-checked script), "current" (your spoken version),
"fact_problems" (from the fact checker: missing, added, changed,
certainty), "style_problems" (from a native editor: quote, why,
suggestion), "too_long_for_the_ear" (sentences over the length limit),
and "stiff_phrases" (written-language phrases found by a counter).
"output_template" shows the marker lines you must return.

Fix exactly those problems and keep everything else word for word:
- fact problems: restore lost facts, remove added ones, correct changed
  values and certainty, always according to "source";
- style problems: follow the editor's suggestion unless it would change a
  fact;
- long sentences: split into short spoken sentences;
- stiff phrases: say them the way people talk;
- "too_long_for_the_length" (when given): the beat is told longer than the film may grow. Tell it
  again in at most "max_words" words: cut repetition and filler and say each fact once, never a
  fact itself.
Keep each beat's length within about 15 percent of the script's speaking
time. A fix may change a sentence's form, never its facts.

Your answer replaces the beat text. The system cuts it at the marker
lines; if any marker is missing or changed, the whole repair is
thrown away and the beats stay as they were, so copy the marker lines
exactly from "output_template". Return only these beats, each under its
marker line, nothing else.
