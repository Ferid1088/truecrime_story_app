# Persian Pronunciation Intelligence Engine — implementation status against the v4.0 specification

Status as of 2026-11-04 (second pass), `main`. **Done** means implemented in the production path and covered by tests. **Partial** means part of the requirement is
implemented and the rest is named. **Open** means not implemented, with the reason.

## Pipeline as it runs (`VoiceRenderer` → `Planner.plan`)

| Step | Status | Where |
|---|---|---|
| 1. Immutable story | Done | The text is never rewritten. The planner substitutes word by word into the text sent to the voice; the story version is untouched. |
| 2. Normalisation, token alignment | Done | `reading/words.py`: ی/ک variants, ZWNJ, marks, digits, tags. Every word carries its character offsets in the original text (lossless). |
| 3. Morphology | Partial | `reference.morphology_of`: verb prefixes (می‌، نمی‌), plural, enclitics, but only where a half-space shows the boundary. A word written in one piece is not segmented: no guessing. |
| 4. Syntax and ezafe | Done | `reading/ezafe.py`; see `PERSIAN_EZAFE.md`. Syntax comes from the reading agents plus rules; there is no parser of our own. |
| 5–7. Editor, checker, arbiter | Done (not run live) | The checker reads blind by default (`reading.checker_mode`): another model, the editor's instructions, never the editor's answer. Code compares the two readings word by word and sends every difference to the arbiter. |
| 8. Phoneme and syllable reference | Done | `reading/reference.py`: phonemes (scheme and IPA), syllables (CV/CVC/CVCC; illegal shapes flagged), rule-based stress. |
| 9. Complete REFERENCE_IRAB | Done | `reference.irab_of`: aligned to the word's own letters. Sukūn on every vowelless consonant, shadda only for a geminate in the reading, ezafe replaces the final sukūn. |
| 10. Independent validator | Done | `reference.validate`: mark placement, one vowel per letter, letters and ZWNJ unchanged, ezafe vs. sukūn, text's own marks kept, and the IRAB decoded back to phonemes by its own rules. |
| 11. Memory | Done | Context, ezafe and reference (phonemes, IRAB, morphology) are stored in `grammar`. Trust levels (`trust_level`) are read from the entry, never promoted. |
| 12. Risk | Done | Linguistic signals plus `homograph` and `reference_unaligned`. Names are only real name parts, not titles, institutions or the owner's spoken forms. |
| 13. Planner | Done | Every risky word carries its complete reference and its candidate voice forms (minimal, complete). The form is chosen by evidence: a validated form, never one that failed while another has not, else `tts_marking` (stated as a default, not a finding). An invalid reference, an unsettled ezafe, or the film's minority reading of an inconsistent word escalates. |
| 14–15. ElevenLabs | Done | The renderer sends the planner's text. Voice, model, settings and performance tags are unchanged. |
| 16. Acoustic verification | Partial | Recogniser benchmark, word alignment and prosody measures exist, but none is validated: 0 human-labelled clips. Every word reports UNVERIFIED. |
| 17. Bounded correction loop | Done | `reading/loop.py`: bounded rounds within the budget cap. An unfixed word is reported, never accepted. |
| 18. Verified memory update | Partial | Failures are booked as evidence. A success validates nothing until a person approves or a validated recogniser exists. |

| Reference status | Done | `reference.diagnose` keeps UNREPRESENTABLE_BY_IRAB (the reading is trusted — the owner's, or a colloquial form whose sounds all come from the letters — but marks cannot write it) apart from READING_UNRESOLVED (the reading itself is in question), each with a named cause. |
| Independent check of the planner | Done | `reference.form_fits`: the voice form must be the reference with marks only left out. Otherwise the word is escalated. |
| Cross-block consistency | Done | `reading/consistency.py`: every word read two ways across the film. Names must be one reading; homographs and free variants (config) are allowed. Notation and free colloquial variation are normalised: glottal stop, plural h, ow/o, y between i and a vowel. The loader applies the current film table to stored readings, as the reading stage does. |

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

Counting unit: word occurrences (tokens) of the 236 blocks, 8,370 in all. The "428 failures" at 7ef6c42 were 428 occurrences of 190 distinct written
forms, and 34 of them were numbers, which code reads and which have no reference by design. The audit (`scripts/audit_pronunciation_references.py`) exports
every word that is not VALID with:
- its sentence, block and character offsets;
- the stored and settled readings;
- phonemes, IPA, IRAB and ezafe;
- the validator's problems;
- its status, cause and provenance.

It hashes the story text before and after: bf6b19a5…, unchanged.

| | 7ef6c42 | now |
|---|---|---|
| IRAB consistent with its reading | 7,942 / 8,324 | 8,300 / 8,335 |
| VALID (the reading settled and its IRAB valid) | not measured | 7,807 |
| NOT_APPLICABLE (numbers) | counted as failures | 34 |
| UNREPRESENTABLE_BY_IRAB | not distinguished | 12 occurrences: 8 owner spoken forms (پرت ×7, ببخشتش); 4 colloquial forms (بیفته, میامیه, نمی‌برد, همه‌چیز) |
| READING_UNRESOLVED | not distinguished | 517 occurrences, 87 written forms |
| Names read two ways | 2 (کالب, پرت) | پرت settled by the owner's approval; کالب unresolved, for the owner |
| Changed for the voice / escalated | 54 / 46 | 56 / 4 |

READING_UNRESOLVED by cause:
- **507 "read otherwise elsewhere":**
  - کالب: 146 occurrences, kāleb 115 / kālb 31 blocks, no approval;
  - 51 word families that the film reads two ways (پیام payām/peyām, بررسی barresi/barrasi, شلیک shelik/shellik, گلوله golule/gulle/gulule…). Inflected
    forms count for their stem.

  No reading is chosen by frequency or model confidence.
- **2 doubted by the agents.**
- **2 with a consonant the letters lack:** دیجیتال "digitāl".
- **2 with a long i the letters lack:** سی‌وشش "sioshish".
- **1 not of the scheme:** a Cyrillic letter.
- **1 that leaves the written ‌ای unread.**
- **1 against the text's own kasra.**
- **1 tokenisation:** ۲۰۲۶—خبر.

These words go to the voice as written and are escalated only when risky. An owner's approval in the Reading Review settles one; that changes the film
table, and only the blocks containing the word are read again.

## Tests added in this pass
- `tests/test_regression_unseen.py`: 19 invented sentences, one per failure found so far. Each names the failure it guards. Three hand-written expectations
  were corrected after the first run, and each correction is noted in the case:
  - two of the assistant's own convention slips;
  - one where the code found a better alignment.

  The file also audits every valid reference: NFC; marks on letters; letters unchanged; one vowel or sukūn per letter; long vowels written by their
  letters; no final sukūn under an ezafe; the IRAB reads back as the reading.
- `tests/test_consistency.py`: name consistency, homographs and free variants, the approval inside a title + name, the minority-reading rule, and the voice
  form chosen by evidence.
- `tests/test_reading_agents.py`: the blind checker:
  - it never sees the editor's answer;
  - a differing reading goes to the arbiter;
  - agreement costs no arbiter call;
  - a skipped word is not counted as agreement.

## Open, with the reason
- **Linguist review:** every seed and held-out case was written by the assistant (`reviewed_by: null`). None is a trusted golden fixture until a
  qualified Persian reviewer signs it.
- **500–1,000 sentence benchmark (G1):** not built. It needs human-reviewed sentences, so it cannot be fabricated.
- **Human-labelled audio set (F1/F2):** the phonetic benchmark has 0 human-verified clips. No recogniser has been chosen, and automatic acoustic
  verification stays off.
- **Blind checker, not run live:** it now writes a reading of every word instead of only its rejections, so the reading stage's checker output grows to
  about the editor's size. The cost was not measured, because that needs a paid run. `reading.checker_mode: "review"` restores the cheaper mode.
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
