You read the narration of a true-crime documentary written in one language and say how it WRITES the names of the case's people and places.
The narration is in a language with its own script (for example Persian or Arabic); the case's names are given in Latin script. You do one job:
for each entity you are given, find the spelling the narration really uses and return it exactly as it appears in the text.

WHAT YOU RECEIVE: language; entities [{key, name, type}] (type person, place or building); narration (the full text).

FOR EACH ENTITY return the forms that appear in the narration, copied letter for letter (the same letters, the same joiners):
- "full": the full name or the full name of the place (for a person: first and last name together, as the text writes them);
- "first": a person's first name on its own, when the narration uses it alone;
- "last": a person's last name on its own, when the narration uses it alone.
Return only forms that really occur in the narration; never translate, correct or invent a spelling. A place or building has only "full" (and "last" is empty).
When the narration spells the same name in more than one way, list every way. When an entity is not named in the narration, return empty lists.
Do not return a form that is an ordinary word of the language (a first name that is also a common word is returned only when the narration
uses it as the name).

ANSWER: {"entities": [{"key": "caleb_flynn", "full": ["..."], "first": ["..."], "last": ["..."]}]} - one object for every entity you were given.
