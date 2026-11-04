# Persian Pronunciation Intelligence Engine — implementation status against the v4.0 specification

Status as of 2026-11-04, `main`. **Done** means implemented in the production path and covered by tests. **Partial** means part of the requirement is
implemented and the rest is named. **Open** means not implemented, with the reason.

## Pipeline as it runs (`VoiceRenderer` → `Planner.plan`)

| Step | Status | Where |
|---|---|---|
| 1. Immutable story | Done | The text is never rewritten. The planner substitutes word by word into the text sent to the voice; the story version is untouched. |
| 2. Normalisation, token alignment | Done | `reading/words.py`: ی/ک variants, ZWNJ, marks, digits, tags. Every word carries its character offsets in the original text (lossless). |
| 3. Morphology | Partial | `reference.morphology_of`: verb prefixes (می‌، نمی‌), plural, enclitics, but only where a half-space shows the boundary. A word written in one piece is not segmented: no guessing. |
| 4. Syntax and ezafe | Done | `reading/ezafe.py`; see `PERSIAN_EZAFE.md`. Syntax comes from the reading agents plus rules; there is no parser of our own. |
| 5–7. Editor, checker, arbiter | Partial | Context reading, ezafe judgement, homograph and register guidance in the prompts. The checker still sees the editor's reading (see Open). |
| 8. Phoneme and syllable reference | Done | `reading/reference.py`: phonemes (scheme and IPA), syllables (CV/CVC/CVCC; illegal shapes flagged), rule-based stress. |
| 9. Complete REFERENCE_IRAB | Done | `reference.irab_of`: aligned to the word's own letters. Sukūn on every vowelless consonant, shadda only for a geminate in the reading, ezafe replaces the final sukūn. |
| 10. Independent validator | Done | `reference.validate`: mark placement, one vowel per letter, letters and ZWNJ unchanged, ezafe vs. sukūn, text's own marks kept, and the IRAB decoded back to phonemes by its own rules. |
| 11. Memory | Done | Context, ezafe and reference (phonemes, IRAB, morphology) are stored in `grammar`. Trust levels (`trust_level`) are read from the entry, never promoted. |
| 12. Risk | Done | Linguistic signals plus `homograph` and `reference_unaligned`. Names are only real name parts, not titles, institutions or the owner's spoken forms. |
| 13. Planner | Done | Selective marks are the minimal form of a validated reference (no sukūns), on the word's own letters. An invalid reference escalates. Every risky word carries its complete reference. |
| 14–15. ElevenLabs | Done | The renderer sends the planner's text. Voice, model, settings and performance tags are unchanged. |
| 16. Acoustic verification | Partial | Recogniser benchmark, word alignment and prosody measures exist, but none is validated: 0 human-labelled clips. Every word reports UNVERIFIED. |
| 17. Bounded correction loop | Done | `reading/loop.py`: bounded rounds within the budget cap. An unfixed word is reported, never accepted. |
| 18. Verified memory update | Partial | Failures are booked as evidence. A success validates nothing until a person approves or a validated recogniser exists. |

## UI (Pronunciation tab → "Why words change")
`GET /api/documentary/versions/{id}/pronunciation-plan` runs the planner dry: no model, no voice, nothing written. For each risky, changed or escalated
word it shows:
- the original word and its sentence (highlighted);
- the Finglish reading, the IPA and the syllables with stress;
- the morphology;
- the REFERENCE_IRAB with the validator's verdict;
- the ezafe class;
- the risk signals with their evidence;
- the decision and its basis;
- the exact ElevenLabs input next to the original;
- the trust level / verification;
- the memory history;
- block playback from the film's narration.

Filters: changed, left for you, high risk kept, all, and a word search.

## Measured (Case 15, dry run, no calls)
- 8,370 words: LOW 6,977, MEDIUM 871, HIGH 514, CRITICAL 8.
- 54 words changed for the voice, 46 escalated.
- Reference valid for 7,942 of 8,324 words (95.4%). The rest are mostly:
  - readings the letters cannot spell, e.g. گلوله read "gulle", آشفته read "āshufte";
  - shapes Persian does not have, e.g. "flin";
  - readings that drop a mark the text writes.

  These are reported, never written.
- Reference cases: `reference.json` (16 seed) and `reference_heldout.json` (12). On both sets:
  - IRAB exact: 28/28;
  - sukūn placement: 125/125 letters;
  - phonemes read back: 28/28;
  - validator: 28/28.

  Metrics are kept apart by `reference.evaluate`.

## Open, with the reason
- **Linguist review:** every seed and held-out case was written by the assistant (`reviewed_by: null`). None is a trusted golden fixture until a
  qualified Persian reviewer signs it.
- **500–1,000 sentence benchmark (G1):** not built. It needs human-reviewed sentences, so it cannot be fabricated.
- **Human-labelled audio set (F1/F2):** the phonetic benchmark has 0 human-verified clips. No recogniser has been chosen, and automatic acoustic
  verification stays off.
- **Independent checker (D2):** the checker still receives the editor's reading. A blind second reading of every block roughly doubles the reading
  stage's model cost, so it waits for the owner's decision.
- **Agent output fields:** the agents give Finglish, ezafe, confidence and questions. Phonemes, syllables, morphology and the IRAB are derived by code
  from that reading (deterministic and the same everywhere), not written by an agent.
- **Prosody (B7):** only word stress, by rule. Phrase stress, focus and intonation boundaries are not modelled. Pauses come from punctuation and the
  readings.
- **Gemination (example 14):** a shadda is written only when the reading geminates. Whether a word geminates in Iranian Persian is the reading agents'
  and the owner's call, not a rule.
- **Reading cache:** Case 15's stored readings predate the new prompts. Running the new prompts on a film is a paid model call for every block and was
  not made.
- **Inconsistent name readings:** the same name can be read differently across blocks (کالب: "kālb" from the film table in B01, "kāleb" in B40 from
  the old cache). The trace shows it; the next reading run with the current table makes it one reading.
