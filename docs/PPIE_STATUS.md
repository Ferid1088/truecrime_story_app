# Persian Pronunciation Intelligence Engine — implementation status against the v4.0 specification

Status as of 2026-11-04 (second pass), `main`. **Done** means implemented in the production path and covered by tests. **Partial** means part of the requirement is
implemented and the rest is named. **Open** means not implemented, with the reason.

## Fresh Persian path (config `ppie.languages`, 2026-11-04)

Persian is voiced by PPIE alone, from scratch on every run. The source is the approved spoken version (`jobs.latest_spoken` never picks a voice-prepare
rewrite; Case 15: v270, not v289). The old voice-prepare stage, the film dictionary, the cross-film lexicon, the pronunciation memory (entries,
interventions, evidence, labelled clips), the stored reading file, owner approvals, reading resolutions and doubt answers of earlier runs, the reading
loop after the voice and the semantic review's corrections and lexicon learning do not reach the planner or the voice. They stay in the database and on
disk for rollback and stay in use for the other languages. A doubt of the reading agents stays in the run: the planner leaves the word as written and
flags it. Numbers in digits are read by `reading/numbers.py` from their context. The narration goes to `v{id}_ppie`; the run's record (`ppie_run.json`)
is what the Pronunciation tab shows. Every Persian style is voiced at speed 1.2, stability 0.2, then played 1.1 times faster. Nothing of a run is kept.
Each run pays for the full reading stage again (Case 15: about EUR 23 on 2026-11-04).

## Review after the voice (`app/documentary/ppie_review`, 2026-11-04)

| Part | Status | Where / evidence |
|---|---|---|
| Approved sources | Done | `sources.py`: narration = spoken version (never a voice-prepare rewrite), its approved Persian final, the approved English final of the English film. Case 15: 270 / 254 / 240. 270 itself has no approval date (noted in every run). |
| English–Persian alignment | Done, uncalibrated | `alignment.py`: sentences with offsets, local BGE-M3, monotonic DP with 1–3 sentence groups per side, numbers and nearby rivals checked; verifier agent optional (billable). Case 15: 164 blocks ALIGNED, 26 PARTIAL, 36 AMBIGUOUS, 10 MISSING_REFERENCE, 0 MISMATCH. No reviewed alignment exists, so no confidence number is given. |
| Independent expected reading | Done (not run live: billable) | `expectation.py` + `prompts/documentary/ppie_review/expect_system.md`: sees the text, its neighbours and the English passage, never PPIE's reading. Readings a person decided can be given instead (`scripts/ppie_review.py --expect`). |
| Analysis copies | Done | `audio_prep.py`: ffmpeg, 16 kHz mono, original and `atempo=0.8`, pitch measured (copy unusable beyond 50 cents), original hashed before/after, temp folder removed. |
| Acoustic review | Done, advisory | `acoustic.py`: every word of the approved text gets its window from the voice's own timing (also when the take was voiced from another text: words matched on their plain form); the expected reading and its rule-made rivals (vowel swaps, epenthesis, ezafe added/dropped, -e said -ye) scored by CTC loss per frame; PASS/FAIL only by a margin; slower copy recorded as a second look. |
| Validation gate | Done; recognizer NOT validated | `validation.py`: human-labelled clips only, hash split calibration/held-out, precision/recall/FPR/FNR per category and speed. 2 labelled clips (the owner's two reports), 0 held out: every decision is advisory. |
| Repair | Done (no paid take made) | `correction.py`: code diagnosis (PPIE reading, inserted vowel/ezafe, number, voice, unknown), candidates (targeted marks, inline `/IPA/`, Persian respelling; no SSML, no dictionary), failed forms not repeated, attempts capped; corrector agent may only reorder. A new take only with the owner's authorization and cost limit; checked by code against the original (target word, every other word, length); the owner keeps or rejects it; keeping it assembles the narration from existing takes with a voice that refuses every request, after keeping the old narration. |
| UI | Done | Pronunciation tab → "Audio review". |

First live result (free): the Case 15 preview voiced from v289, reviewed against approved v270 with the owner's two readings: both reported errors are found as suspected (نوه‌هاش heard as novehāsh, margin 0.32; عمر heard as omer, margin 0.27), advisory. 49 blocks of that preview were voiced from another text than the approved one and are reported as such. A full review of 236 blocks took 13 minutes and 3.5 GB of memory on this machine.

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

## Resolving the words the film reads in more than one way (`reading/resolution.py`)
The rules, in order:
1. **The owner's approval.**
2. **Homograph or listed free variant:** kept, but only if every candidate is one of its listed readings.
3. **Source syllables:** a name's English source spelling (found in the English story by its consonants) has the same number of syllables as exactly one
   candidate.
4. **Orthography:** exactly one candidate can be written on the approved text's letters.
5. **Enclitic vowel alternation:** -esh/-ash; kept as a variant.
6. **Otherwise:** the owner decides, once per family (Pronunciation → Conflicting readings).

Frequency and model confidence are never evidence. Notation is normalised before comparing, while the original readings are kept in the audit:
- glottal stop;
- the plural h;
- hyphens;
- an ezafe written without its hyphen;
- y and ey glides;
- ow/o;
- n→m before b.

A resolution is stored per case in `reading_resolutions` (migration 0089) with its rule, evidence and every candidate. The film table applies it below the
owner's approval, so only blocks containing the word change their cache key: 156 of 236 for Case 15, mostly because of کالب. An owner's decision is stored
the same way (`owner_decision`). It is not an approval and does not respell the word for the voice.

Case 15:
- **Notation only:** 49 words, 307 occurrences.
- **Resolved by rule:**
  - orthography: پلیس polis, گلوله golule (with its plurals), شانزدهم shānzdahom, زندگیش zendegish, نشیمن neshiman, آشفته āshofte;
  - source syllables: کالب kāleb ("Caleb", 2 syllables; kālb has 1);
  - the owner's approval: پرت pat.
- **Kept as variants:** 16 (11 homographs or free variants, 5 enclitic alternations).
- **For the owner:** 36 families, in `data/audits/case15_fa_v289_resolution/owner_review.md` with the assistant's recommendations, which are not applied.

Bugs found while resolving, fixed with tests:
- A final y was dropped after any vowel. This made sa'y into "sa" and key into "ke", and gave two wrong resolutions before the fix.
- کی was missing from the homographs.
- The unhyphenated-ezafe rule misfired on an ayn's vowel (موقع) and on an ezafe the text already writes (اداره‌ی).
- A homograph was kept although one candidate was none of its readings (کشتی keshti).
- A resolved name still counted as unresolved.

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
