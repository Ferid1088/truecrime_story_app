You hear a very short clip of a Persian (Farsi) narration: ONE word (or one name of several words) and usually the start of the word after it. Listen only to that word. Do not guess from the text: answer from what you hear.

# Input (JSON)
- `word`: the word as written in the text the voice was given.
- `kind`: "ezafe" (the word carries an ezafe), or "reading" (the word has a chosen pronunciation).
- `following_word`: the word after it, as context only.
- `expected_reading`: for kind "reading", how the word must sound (a respelling or an IPA reading).
- `options`: the possible things you may hear. Choose exactly ONE option id.

# How to answer
Listen to the word alone, then pick the option that matches what you hear. Pick "cannot_tell" when the clip is too short, too noisy or the word is cut off: a wrong guess is worse than no answer. Do not pick an option because it looks likely; pick it because you hear it.

For an ezafe: after a word that ends in a consonant the ezafe is one short "e" sound; after a word that ends in a vowel it is a "ye" sound. Listen for an extra "ye" after the "e", for a doubled (lengthened) last consonant, and for a missing ezafe.

Return JSON only:
{"choice": "<one option id>", "heard": "the word in Latin letters with every vowel, as you hear it", "note": "one short sentence"}
