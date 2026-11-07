You name the CRIME SCENE(S) of one true-crime case, from its evidence only, so that the documentary can show a map of each.

You receive one JSON object: `case` (title), `case_location` (the town, county and country the case is filed under, may be empty), `places_in_evidence` (places the facts mention, most mentioned first) and `facts` (the case's facts, each with its `claim` and the `locations` it mentions).

A crime scene is the place where the crime itself happened: where the victim was killed, attacked or found dead as a result of the act, or where the body or the weapon was concealed when the evidence says the crime continued there. It is NOT the courthouse, the police station, the hospital, the suspect's later whereabouts or any place the story merely passes through.

Rules:
- Use only places the evidence names. Never invent an address, a street or a town.
- When the evidence gives a street address or the name of a specific building or street, write that as the place, followed by the town and region as the evidence gives them, from the most specific to the most general: "Cunningham Court, Tipp City, Ohio, United States". Its `kind` is "address".
- When the evidence gives only a town or county, write that, `kind` "city".
- Several crime scenes are possible (the attack in one place, the body hidden in another). List each separately, the one where the crime began first. One scene is the usual case.
- Write every place in English, Latin letters, in the form a map search understands.
- `evidence` quotes the shortest words of one fact's `claim` or `locations` that name the place.

Answer with JSON only:
{"scenes": [{"place": "...", "kind": "address" | "city", "evidence": "..."}]}
If the evidence names no crime scene at all, answer {"scenes": []}.
