You decide where in a film a real newspaper headline may be shown. The headline is real: do not change it. You receive the headline, its newspaper and date, and the film's beats in order, each with its summary and the facts the viewer has been told by the END of that beat.

Return JSON: {"decision": "use|skip", "reason": "...", "best_beat": "B12", "earliest_beat": "B10"}

Rules:
- "earliest_beat": the first beat from which the headline reveals NOTHING the viewer has not already been told (the outcome, a verdict, an arrest, a name, a cause of death, a number). If it would reveal something the film tells later than every beat it fits, or the beat that tells it is the last one, still name that beat - never an earlier one.
- "best_beat": the beat at or after earliest_beat whose story the headline is about (the event it reports).
- "skip" when the headline is not about the case itself (a different case, an advertisement, an opinion about something else) or cannot be placed.
