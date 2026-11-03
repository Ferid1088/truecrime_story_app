# Pronunciation dictionary check and request trace

`app/documentary/pronunciation_trace.py`, config `documentary.json` → `pronunciation_trace`.

Before every take the rules of the film's dictionary are checked against the **exact final text** (after scrubbing, the reading loop's IPA and every
fix). Each rule gets one status per block: `matched`, `reconciled` (the text spells the word differently), `unresolved` (the word was in the text the
dictionary was made from and is gone), `conflict`, `shadowed`, `invalid`, `duplicate`, `absent` (not in this block). The text is never changed to make
a rule match; a vowel mark the rule has is a sound and is never ignored.

Modes (`pronunciation_trace.mode`):
* `trace` (default): the request is unchanged (the cache key of every take stays), the result is recorded.
* `enforce`: the block is sent a dictionary made for its final text, with only the rules that act on it (reconciled rules follow the text's spelling;
  conflicting, lost, shadowed and invalid rules are left out). One dictionary per rule set (fingerprint), made under a lock, kept in
  `block_dictionaries.json`, never changed afterwards. A take then has another cache key than one made with the film dictionary.

Trace: `pronunciation_trace.json` next to the narration lists, per take, the text, voice, model, settings, seed, language code, neighbours, dictionary
locators, rule results with exact occurrences, cache key and request id. The take sidecar (`blocks/*.json`) now also keeps the locators and the rules.

Dry run, nothing paid and nothing written in the film: `python scripts/audit_pronunciation_rules.py CASE VERSION [LANG] [OUT.json]`.
