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

| | 7ef6c42 | now |
|---|---|---|
| LOW / MEDIUM / HIGH / CRITICAL | 6,977 / 871 / 514 / 8 | 6,593 / 1,294 / 482 / 1 |
| Changed for the voice | 54 | 57 (46 owner respellings, 8 selective marks, 3 ezafe marks) |
| Escalated | 46 | 2 |
| Words without a valid reference | 428 | 71 |
| Names read two ways | 2 (کالب, پرت) | 0 |
| Ordinary words read two ways (not homograph or free variant) | not checked | 49 (listed for review) |

MEDIUM rose because the new consistency signal (+1) is now counted. A MEDIUM word is not changed.

The 71 words without a valid reference:
- **34 numbers:** read by code.
- **8 owner-approved spoken forms:** پرت → "pat" ×7, ببخشتش. The letters do not spell these readings; the owner's respelling path sends them.
- **15 colloquial forms the spelling does not show:** شانزدهم "shunzdahom" ×5, گلوله "gulle" ×7, نمی‌برد, همه‌چیز, پلیس "pulis".
- **8 agent reading errors:** دیجیتال "digitāl" ×2, آشفته "āshufte", نشیمن "neshimān", بیفته "biofte", الکترونیکی‌ای (the suffix is dropped), زیادی "ziyād", نرفته (a Cyrillic letter).
- **1 reading against a mark the approved text writes:** به‌خودیِ‌خود.
- **3 joined number words** whose و is said o (سی‌ویک, سی‌وشش). Not supported: a و that starts a written piece is read as v.
- **1 tokenisation:** ۲۰۲۶—خبر is one token, because the em-dash has no spaces.
- **1 hiatus notation:** میامیه.

Root causes fixed in this pass:
- **Generator and decoder gaps:**
  - tanwin;
  - آ and a compound's alef inside a word;
  - a geminate versus two letters across a half-space;
  - a yeh for i+y with a shadda;
  - ayn and hamza silent, as a glide, or carrying a vowel;
  - the plural's h;
  - n→m before b;
  - ی said ā (dagger alef);
  - a word-initial ayn before a long vowel;
  - one written word read as several;
  - a mark the approved text writes is now a constraint on the alignment.
- **Scheme ambiguities, decided by the letters:** gh/zh/sh/kh versus two letters; ey versus e+i; "oo" means u.
- **A doubled ezafe:** for readings written without the hyphen (edāreye-ye).
- **Stale reading cache:** blocks read before the film table changed kept old name readings (کالب kāleb ×160 against the table's kālb). The loader now applies the current table.
- **Film table:** the owner's approval of a name did not reach a title + name entry (قاضی پرت kept "part").
- **Ezafe evidence:**
  - the approved text's written ezafe now outranks an agent reading that leaves it out;
  - a punctuation rule outranks an agent reading that puts an ezafe before a comma;
  - a copula written with its own ه is not an ezafe;
  - رو said "ru" takes an ezafe.

Two remain escalated, rightly:
- **سعی:** the film reads it two ways, sa'y / sa'i.
- **شانزدهم "shunzdahom":** colloquial; the letters cannot show it.

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
