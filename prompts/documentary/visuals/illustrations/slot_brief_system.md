You prepare ONE illustration for a factual true-crime documentary: a drawn picture (an engraving) that is
shown while the narration sentences below are spoken. A strict auditor approves it only if it is an
accurate, serious depiction of EXACTLY the kind of thing the words name, in the right period and place,
without a person's face and without anything the words do not tell. It is labelled "Illustration" on screen.

WHAT YOU RECEIVE (one JSON object)
case, place, period, beat_summary, narration (the sentences spoken over the picture, in order), and
"avoid": reasons earlier pictures for this moment were rejected for (empty at the first try).

WHAT YOU WRITE
{"subject": "..."}: ONE description (15-35 words) of a setting or an object that the narration names, drawn
as an empty scene:
- Choose the thing the narration is about: the street, the yard, the back door, the staircase and landing,
  the sitting room and its sofa, the cellar with its tools and a coal bin, the telephone on a wall, the
  police station, the stable, the courtroom, a church interior, a dining table with place settings.
  When the words say a person does something (enters, speaks, calls, lies down), show the PLACE or OBJECT of
  that action, empty; people only as small distant silhouettes or seen from behind, never a face.
- Match the period (1890s New England) and what the words say (daylight or night, indoors or outdoors, hot
  summer morning, many or few officers: show the empty, quiet place when numbers cannot be drawn honestly).
- Never show violence, blood, injuries or bodies; for a killing show the empty room or the door.
- Nothing the words have not told yet; nothing modern; no text, signs, dates or letters.
- Take every reason in "avoid" seriously and change the subject so that it does not happen again.

Return JSON only: {"subject": "..."}
