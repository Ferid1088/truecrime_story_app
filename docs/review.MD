# MASTER IMPLEMENTATION PROMPT
## Persian Pronunciation Intelligence — Semantic Alignment, Acoustic Review & Autonomous Correction
### Production Integration for `truecrime_story_app`

You are the lead AI engineer, computational linguist, speech-processing engineer, and quality architect working on the existing production application.

**Mission:** Implement a complete, reliable, independent Persian pronunciation verification and correction system. It must detect actual pronunciation errors in ElevenLabs-generated speech, understand the intended meaning using the approved English story, correct confirmed errors intelligently, and verify the resulting audio.

This is a production implementation task, not a prototype, standalone experiment, or documentation-only assignment.

Do not invent results, claim unsupported accuracy, or treat an agent's linguistic opinion as proof of what the audio actually contains.

---

# A. Non-Negotiable Constraints

1. **Persian only (`fa`).** Do not modify the behavior of English, German, Arabic, Spanish, Turkish, or other language pipelines.
2. Work in the existing repository at `/Users/feri/truecrime_story_app` if available. Inspect the real architecture, APIs, database, model adapters, TTS provider, UI, and tests before making changes.
3. Create a dedicated feature branch from the current working state. Preserve existing uncommitted work. Never merge or push without explicit authorization.
4. Preserve all existing functionality outside Persian pronunciation processing, including story research, writing, Host, performance direction, music, visual planning, and rendering.
5. The owner-approved Persian Final Story is **immutable**. No agent may rewrite its narrative, meanings, facts, paragraphs, or canonical text.
6. The owner-approved English Final Story is also immutable.
7. For Case 15, inspect the approval metadata and identify the actual approved Persian source. Previous investigations identified version 270 as approved and version 289 as a later Voice Prepare derivative. Verify this in the real app; never assume the latest version is approved.
8. Do not use legacy Voice Prepare rewrites, the previous film pronunciation dictionary, the cross-film lexicon, historical reading decisions, or old pronunciation caches.
9. Treat every new production run as a first encounter with the approved story. Do not reuse or persist pronunciation decisions, corrections, alignments, or acoustic-review memory between runs.
10. Persistent application code, configuration, and general linguistic algorithms are allowed. Run-specific linguistic decisions and alignment data must remain isolated to the active run.
11. Preserve existing approved production assets. Never overwrite or delete originals. Temporary analysis files must be isolated and cleaned up safely.
12. Do not make any paid ElevenLabs requests, regenerate production audio, or incur other billable model/API costs without explicit owner authorization.
13. Maintain these exact Persian voice settings:
   - Model: existing configured `eleven_v4`
   - ElevenLabs speed: `1.2`
   - Stability: `0.2`
   - Final audio post-speed: `1.1`
   - Preserve other voice parameters and existing performance tags.
14. Do not silently change pronunciation settings to obtain better benchmark results.
15. Never automatically approve audio when the evidence is insufficient.

---

# B. Inspect the Existing Application

Before implementing:

- Trace the complete Persian text-to-audio pipeline.
- Identify the canonical approved Final Story source and how approval is represented.
- Locate the English Final Story and existing relationships between story versions.
- Inspect all Persian audio-block creation, segmentation, TTS preparation, and block identifiers.
- Locate existing PPIE agents, pronunciation planner, reading checker, dictionaries, phoneme alignment, review code, audio processing, and error reporting.
- Inspect the current ElevenLabs adapter and dictionary functionality.
- Identify how the frontend exposes narration blocks, audio playback, pronunciation review, and owner approval.
- Find existing pronunciation benchmarks and test fixtures.
- Determine whether multilingual text alignment, cross-lingual embeddings, or story-beat IDs already exist.

Produce an internal dependency map. Reuse functioning infrastructure rather than introducing duplicate pipelines.

Do not delete obsolete paths until their replacements are implemented and verified.

---

# C. New End-to-End Architecture

Implement the following orchestration:

```text
OWNER-APPROVED ENGLISH FINAL STORY
                  |
                  v
        English Semantic Units
                  |
                  v
            Alignment Engine
                  ^
                  |
        Persian Semantic Units
                  ^
                  |
OWNER-APPROVED PERSIAN FINAL STORY
                  |
                  v
       Persian Narration Blocks
                  |
                  +-----------------------+
                  |                       |
                  v                       v
       PPIE Preparation         Independent Reading Agent
                  |                       |
                  v                       v
          TTS Input Draft         Expected Phonemes
                  |                       |
                  v                       |
       ELEVENLABS — SPEED 1.2             |
                  |                       |
                  v                       |
        Original Generated Audio          |
                  |                       |
                  +----------+------------+
                             |
                             v
                Local Audio Preparation
                  Original + Slower Copies
                             |
                             v
                    Acoustic Analysis
                             |
                             v
                Independent Review Agent
                             |
                      +------+------+
                      |             |
                     PASS       FAIL / UNCERTAIN
                      |             |
                      |             v
                      |      Correction Agent
                      |             |
                      |      Corrected TTS Input
                      |             |
                      |      Budget Authorization
                      |             |
                      |      Targeted Regeneration
                      |             |
                      |             v
                      |       Acoustic Re-review
                      |             |
                      +-------------+
                             |
                             v
                    Approved Audio
                             |
                             v
                   Post-Speed 1.1
                             |
                             v
                   Final Film Pipeline
```

All relevant stages must operate on the real existing narration-block pipeline.

---

# D. English–Persian Semantic Alignment Engine

## Objective

For every Persian narration block, find the corresponding English passage that expresses the same meaning.

Do not assume matching block IDs, sentence counts, paragraph boundaries, or identical text order.

## D1. Source selection

Read only the owner-approved English and Persian Final Stories.

Verify source identity, approval status, document completeness, and version consistency.

Reject missing, unapproved, or ambiguous sources rather than silently selecting a substitute.

## D2. Segmentation

Create contextual semantic units for both stories.

Preserve references to original character offsets and source spans.

Segment using:

- Sentence boundaries
- Paragraph boundaries
- Story beats and narrative events
- Speaker or quotation changes
- Named entities
- Dates, times, locations, and quantities
- Existing chapter or scene metadata, when available

Do not modify canonical source text during segmentation.

## D3. Candidate matching

Use an existing suitable multilingual embedding model or evaluate local candidates such as multilingual E5, BGE-M3, or LaBSE.

Prefer local inference. Do not require paid external translation or embedding APIs.

Generate multiple candidate English spans for every Persian span, including adjacent-span combinations.

Use a monotonic sequence-alignment algorithm where the narrative order supports it.

Support:

- One-to-one
- One-to-many
- Many-to-one
- Many-to-many
- Missing or expanded passages

Combine semantic similarity with named entities, event chronology, quantities, and narrative context.

A high embedding score alone must not be considered proof of correct alignment.

## D4. Independent alignment verification

Create a **Semantic Alignment Verifier Agent** that receives candidate pairs and verifies whether they describe the same events and meaning.

It must detect:

- Different events with similar wording
- Added or omitted information
- Names or identities that do not match
- Incorrect numbers or dates
- Translation inconsistencies
- Metaphorical or idiomatic differences
- Reordered narrative passages

Return one of:

`ALIGNED`, `PARTIAL`, `AMBIGUOUS`, `MISMATCH`, or `MISSING_REFERENCE`.

Do not invent an English match merely to achieve complete coverage.

## D5. Attach alignments to audio blocks

Associate each Persian audio block with the exact Persian source span and matching English source span or spans.

If audio-block segmentation differs from semantic segmentation, map through immutable source offsets or equivalent provenance identifiers.

Every block should have:

```json
{
  "block_id": "example",
  "persian_source_span": {
    "start": 0,
    "end": 0
  },
  "english_source_spans": [],
  "alignment_status": "ALIGNED",
  "alignment_confidence": null,
  "semantic_evidence": [],
  "unresolved_issues": []
}
```

The fields above are a conceptual contract; adapt the final schema to the application's established conventions.

Confidence must be calibrated against reviewed examples, not invented by an LLM.

Alignment results must remain scoped to the current run.

---

# E. Independent Persian Reading Agent

## Objective

Determine how the Persian words should be pronounced **independently of PPIE's previous decision**.

The agent must not treat the existing PPIE reading, generated TTS input, or ElevenLabs pronunciation dictionary as authoritative.

### Agent inputs

- Immutable Persian source block
- Neighboring Persian text
- Matched English passage and alignment status
- Relevant story-level context
- Names, events, and factual references
- General Persian linguistic rules

The expected reading must be derived independently before comparing it with PPIE.

### Responsibilities

Analyze:

- Context-dependent Persian short vowels
- Ezafe
- Homographs
- Proper names
- Colloquial pronunciation
- Compound words
- Word-final consonants
- Syllable boundaries
- Consonant clusters
- Foreign names and transliterations
- Numbers, dates, years, telephone numbers, and identifiers
- Contextual ambiguity between readings

For each word, produce an expected phoneme sequence, alternative plausible readings when genuinely ambiguous, and a linguistic justification.

Use a consistent phonetic representation with clearly documented normalization rules.

Do not assume that identical Persian spellings always have identical pronunciations.

### Persian-specific rules

- Distinguish consonant-final Ezafe `-e` from vowel-final Ezafe `-ye`.
- Do not create an additional `-ye` after an already correct consonant-final Ezafe.
- Preserve the distinction between `omr` and an incorrect extra-vowel pronunciation such as `omer`.
- Distinguish contextually correct readings of `نوه‌هاش`.
- Treat final Sukūn as an explicit phonetic-reference convention where appropriate, not as a blanket addition to the text sent to ElevenLabs.
- Distinguish full-reference اعراب‌گذاری from minimal TTS-specific markings.
- Do not globally rewrite canonical Persian letters to approximate phonetics.
- Interpret `911` according to its meaning as a phone number when the context requires digit-by-digit reading.

If the English alignment is ambiguous, rely on available Persian context and record the unresolved ambiguity instead of guessing.

---

# F. ElevenLabs Generation and Audio Preservation

Use the production model and voice configuration.

Generate audio at speed `1.2` and stability `0.2`.

Immediately preserve an immutable original audio buffer or asset reference for the current generation.

Never modify that original for analysis.

The review stage must occur before the final post-speed transformation.

Retain performance tags, narration style, pauses, voice identity, and existing block metadata.

Do not regenerate audio solely to obtain a slower recording.

---

# G. Free Local Acoustic Analysis

## G1. Audio preparation

Use local, free software such as FFmpeg.

Create independent analysis copies:

- Original generated audio, unchanged, as the primary signal
- A temporary pitch-preserving slower version, initially `0.8×` playback speed
- Optionally other speeds if benchmarking demonstrates additional value

For FFmpeg, evaluate the `atempo` filter.

An example analysis transformation is:

```bash
ffmpeg -i original.wav \
  -af "atempo=0.8" \
  -ac 1 -ar 16000 \
  analysis_08.wav
```

The resulting analysis copy is not used in the final film.

Slowing audio by 0.8 does not guarantee better recognition. It changes playback duration; do not claim it precisely reverses ElevenLabs' internal speed control.

Resample appropriately for the selected acoustic model.

Verify that pitch is preserved within the filter's practical tolerances.

Do not change sample-rate metadata as a substitute for proper time stretching.

## G2. Acoustic model

Inspect the existing implementation using:

`facebook/wav2vec2-xlsr-53-espeak-cv-ft`

Verify its actual Persian phoneme-recognition ability.

Do not assume that multilingual training guarantees good recognition of Persian short vowels.

The model outputs phonetic sequences, not reliable proof that each expected word was spoken correctly.

Evaluate whether local alternatives, model-specific acoustic scoring, or a complementary Persian ASR model improve error detection.

All audio preparation and local acoustic analysis must operate without paid cloud speech APIs.

## G3. Analyze every spoken word

Attempt full coverage of Persian narration, including words not flagged by PPIE.

Use word localization and contextual acoustic windows.

Focus especially on:

- Short vowels
- Extra or missing vowels
- Ezafe
- Names
- Number readings
- Omitted or repeated words
- Pronounced versus silent letters where relevant
- Unnatural inserted syllables

Do not treat voice-direction tags or non-speech sounds as spoken words.

## G4. Compare competing pronunciations

For a disputed word, compare the acoustic evidence for multiple plausible phoneme sequences.

Example:

```text
Persian: نوه‌هاش

Expected: navehāsh
Alternative: novehāsh

Question:
Does the acoustic evidence distinguish the first
vowel as /a/ rather than /o/?
```

Use candidate likelihoods and validated margins where possible.

Avoid depending solely on greedy phoneme transcription or a single forced alignment: a forced aligner can align an incorrect transcript to audio and still produce plausible timestamps.

Measure acoustic confidence empirically.

## G5. Multiple-speed review

Run the evaluation on the original signal.

Evaluate the slower copy as a secondary signal.

Benchmark whether using both improves real error detection.

Do not use a voting rule that assumes both versions are independent recordings; they originate from the same audio.

If the slowed copy decreases accuracy, disable it automatically through evidence-based model configuration.

Never modify the final narration because of analysis-speed experiments.

---

# H. Independent Acoustic Review Agent

## Agent role

You are an independent Persian pronunciation auditor.

Your responsibility is to identify pronunciation errors supported by acoustic evidence, not simply to agree with the pronunciation proposed by PPIE.

You receive:

- Approved Persian source block
- Verified English semantic reference, when available
- Independently derived expected phonemes
- Actual audio analysis
- Alternative candidate phoneme scores
- Word timestamps or time windows
- Acoustic model validation information

You must distinguish what the story means from what the speaker audibly pronounced.

Never infer the actual spoken vowels solely from written text or a translation.

## Decisions

For every word:

- `PASS`: Sufficient validated evidence supports the intended pronunciation.
- `FAIL`: Sufficient validated evidence supports a different or erroneous pronunciation.
- `UNCERTAIN`: Evidence does not distinguish the plausible readings reliably.
- `NOT_EVALUABLE`: The audio, alignment, or model is unsuitable for an assessment.

Only verified evidence may produce an automatic `PASS` or `FAIL`.

Before validation of the acoustic models, all automated decisions must run in advisory mode.

The review must not conceal errors simply because PPIE previously classified a word as safe.

---

# I. Autonomous Pronunciation Correction Agent

## Agent role and internal instruction

You are an expert Persian phonologist, language-aware TTS engineer, and autonomous pronunciation repair specialist.

Your objective is to repair confirmed pronunciation errors in the existing ElevenLabs narration.

You are **not** a subordinate of PPIE.

PPIE may itself be responsible for the error.

You must independently establish the intended reading before proposing a correction.

You may challenge, reject, or replace any PPIE-generated pronunciation decision.

Your job is not merely to add diacritics repeatedly. Your job is to identify why the actual speech was wrong and choose the most suitable correction mechanism for that specific context and voice.

## I1. Inputs

Receive:

- Original Persian word and complete block
- Matched English context
- Independent expected phonemes
- PPIE's intended phonemes and TTS transformation, for diagnosis only
- Actual acoustic findings
- Audio time window
- ElevenLabs model and voice settings
- Previous correction attempts from this active run only

Do not receive historical cross-film pronunciation memories.

## I2. Diagnose the source of the error

Consider at least:

1. PPIE selected an incorrect reading.
2. PPIE selected the correct reading but supplied insufficient TTS guidance.
3. PPIE or a formatter inserted an extra vowel or Ezafe.
4. The ElevenLabs model misinterpreted a correct input.
5. An IPA or dictionary rule matched the wrong span.
6. A number was incorrectly normalized.
7. An independent reading agent is wrong.
8. The acoustic recognizer produced a false positive.
9. The English–Persian alignment was incorrect or incomplete.
10. A performance or contextual effect obscured the actual phonemes.

Report the likely cause and evidence.

Where the cause cannot be determined, do not pretend it is known.

## I3. Create independent correction candidates

For verified linguistic targets, produce alternative TTS-only representations.

Available strategies must be capability-checked and may include:

**Strategy A — Targeted diacritics**

Apply localized Fatha, Kasra, Damma, and Sukūn as appropriate.

Avoid unnecessary global diacritization.

Do not create duplicate Ezafe sounds.

**Strategy B — Inline IPA**

For Eleven v4, use standard IPA between forward slashes:

`/IPA_transcription/`

Generate IPA from the independently verified expected reading.

Apply to the smallest contextually sufficient word or phrase.

Check that the model does not literally speak the notation and does not damage surrounding pronunciation.

**Strategy C — Scoped pronunciation-dictionary rule**

Use an IPA rule in a freshly created, appropriately scoped dictionary when the application supports it and the correction has a context-safe match.

Avoid global rules that corrupt homographs elsewhere.

Do not attach legacy dictionaries.

If dictionary creation creates persistent provider-side assets, clean them up after the authorized run and avoid creating them unnecessarily.

**Strategy D — Alternative TTS spelling**

Produce a carefully chosen phonetic spelling that the particular ElevenLabs voice is more likely to pronounce correctly.

This spelling exists only in the TTS working representation.

Never replace the canonical Persian Final Story text.

**Strategy E — Contextual phrasing**

When necessary, evaluate whether a different TTS-only representation of the immediate phrase improves pronunciation without changing its lexical content, factual meaning, or natural delivery.

Do not omit, add, or substitute story facts or narrative wording.

Do not use unsupported SSML phoneme syntax for Eleven v4.

## I4. Candidate selection

Choose corrections based on:

- Linguistic correctness
- Context specificity
- Supported ElevenLabs capabilities
- Minimal modification
- Low risk of changing neighboring words
- Preservation of rhythm and performance
- Prior candidate outcomes from the active run
- Authorized cost

Do not repeatedly submit the same failed correction.

If PPIE caused the error, correct the independent run-level reading decision before producing new TTS input.

Record the cause within the active run so other occurrences can be re-evaluated, but never automatically propagate a pronunciation across contexts with different readings.

## I5. Targeted audio regeneration

For a confirmed error, prepare regeneration of only the affected block.

Preserve the exact voice, model, speed, stability, and remaining approved parameters.

Before any ElevenLabs API call:

- Check owner authorization.
- Estimate billable characters and likely cost.
- Apply the approved run-level and case-level budget policy.
- Prevent unauthorized retries and duplicate submissions.

If not authorized, show the correction proposal and keep the current recording unchanged.

## I6. Re-review

After an authorized regeneration:

- Analyze the newly generated audio.
- Verify the originally incorrect pronunciation.
- Recheck every other word in the regenerated block.
- Check naturalness, timing, pauses, and expressive delivery.
- Detect any newly introduced pronunciation errors.
- Preserve the previous recording until the replacement passes all gates.

If the new recording is worse, reject it.

A correction succeeds only when the new audio provides stronger, validated evidence of correct pronunciation without unacceptable regressions.

## I7. Intelligent retry policy

Support configurable, bounded retry attempts with a clear stopping policy based on quality evidence, remaining budget, and user-defined limits.

Do not hard-code a single pronunciation strategy or blindly repeat regeneration until success.

If all viable strategies fail, mark the block for owner review rather than silently approving it.

---

# J. Independent Correction Verification

The correction-generating agent must not approve its own work.

Create a distinct verifier that:

1. Receives the original and corrected audio evidence.
2. Compares acoustic findings against the independent expected reading.
3. Examines neighboring words for regressions.
4. Evaluates whether the correction changed the actual spoken content.
5. Checks whether the correction preserved natural narration and delivery.
6. Reports uncertainty instead of manufacturing confidence.

No agent should be allowed to mark an audio block as acoustically correct solely through text reasoning.

A corrected pronunciation must pass the same acoustic criteria as the original generation.

---

# K. Runtime Data Contracts

Implement typed, validated data structures compatible with the existing backend.

At minimum, represent these concepts:

### K1. Alignment result

```json
{
  "block_id": "fa-block-id",
  "persian_span": {},
  "english_spans": [],
  "status": "ALIGNED",
  "evidence": [],
  "ambiguities": []
}
```

### K2. Pronunciation expectation

```json
{
  "word_id": "word-id",
  "canonical_text": "نوه‌هاش",
  "expected_phonemes": "...",
  "alternative_phonemes": [],
  "contextual_reason": "...",
  "source": "independent_reading_agent"
}
```

### K3. Acoustic review

```json
{
  "word_id": "word-id",
  "audio_window": {},
  "expected_phonemes": "...",
  "detected_candidates": [],
  "status": "UNCERTAIN",
  "acoustic_evidence": [],
  "model_validation_status": "UNVALIDATED"
}
```

### K4. Correction proposal

```json
{
  "block_id": "fa-block-id",
  "word_id": "word-id",
  "suspected_root_cause": "tts_misreading",
  "verified_target_phonemes": "...",
  "strategies": [
    {
      "type": "inline_ipa",
      "tts_text": "...",
      "reason": "..."
    }
  ],
  "owner_approval_required": true,
  "estimated_cost": null,
  "status": "PROPOSED"
}
```

### K5. Verification result

```json
{
  "block_id": "fa-block-id",
  "original_audio_review": {},
  "candidate_audio_review": {},
  "target_error_resolved": null,
  "new_errors_detected": [],
  "final_decision": "UNCERTAIN"
}
```

All schemas must include actual source identities, run IDs, and useful provenance where appropriate.

Separate linguistic confidence, semantic alignment confidence, acoustic confidence, and correction confidence.

Do not combine unrelated confidence values into a misleading single percentage.

All run-specific records must have controlled lifecycle cleanup.

---

# L. Human-Labeled Acoustic Benchmark

This is mandatory before enabling automatic pronunciation rejection or retakes.

Build an evaluation workflow using human-confirmed Persian pronunciation examples.

Include:

- Correct and incorrect short vowels
- Correct and incorrect Ezafe
- Extra syllables
- Missing syllables
- Names
- Number readings
- Colloquial Persian
- Normal and dramatic narration
- Different acoustic contexts
- Correctly pronounced words that resemble common errors

Use the owner's previously identified problem categories as test targets, but do not treat model predictions as human labels.

Test original and time-stretched audio.

Measure:

- Detection precision
- Recall
- False-positive rate
- False-negative rate
- Word-level localization quality
- Confidence calibration
- Performance by error type
- Performance under different analysis speeds

Use separate calibration and held-out test material.

Do not use the same tiny sample to select thresholds and claim validated accuracy.

The earlier four-clip result is insufficient to establish production reliability.

Until validation passes the configured acceptance criteria:

- Run the system in advisory mode.
- Report suspected errors.
- Allow manual listening.
- Do not automatically reject otherwise usable audio.
- Do not automatically generate billable correction attempts.

If the existing wav2vec2 model proves inadequate, evaluate a stronger locally executable alternative before enabling automation.

---

# M. Frontend Integration

Integrate the system into the existing Persian Pronunciation interface.

Do not build a disconnected dashboard.

For each block, display:

- Persian canonical text
- Matched English context
- Alignment status
- Original audio playback
- Suspected problematic words
- Expected pronunciation
- Acoustic evidence
- Review decision
- Likely error source
- Correction proposals
- Current correction strategy
- Original-versus-candidate audio comparison
- Estimated regeneration cost
- Owner approval controls

Provide a clear distinction between:

- Linguistic disagreement
- Acoustically confirmed error
- Uncertain acoustic observation
- Successfully verified correction

Allow the owner to listen to original and candidate audio and decide which version to accept.

Show run-level coverage, review counts, uncertainty counts, correction attempts, and authorized costs.

Do not imply that a green result is acoustically verified when the model has not met the validation threshold.

The UI must not silently modify or overwrite approved story content.

---

# N. Real Application Testing

Implement unit, integration, and end-to-end tests.

## N1. Alignment tests

Test:

- Equivalent English/Persian passages
- Different sentence boundaries
- Different paragraph boundaries
- One-to-many and many-to-one mappings
- Translated idioms
- Similar but incorrect passages
- Missing English passages
- Repeated events
- Chronological ambiguity
- Named entities and number mismatches
- Case 15 approved-source selection

## N2. Linguistic tests

Test:

- Consonant-final versus vowel-final Ezafe
- `dar-e` versus `dare-ye`
- `omr` versus `omer`
- `navehāsh` versus `novehāsh`
- Context-dependent homographs
- Colloquial Persian
- Names
- Phone number `911`
- Persian and Latin numerals
- Preservation of original story characters
- Legacy dictionary isolation

## N3. Audio tests

Test:

- Original audio never modified
- Correct time stretching
- Pitch preservation within measurable tolerances
- Correct sample-rate conversion
- Word-window extraction
- Candidate phoneme comparison
- Acoustic uncertainty handling
- Correct cleanup of temporary files
- Validation-gated decisions
- Regenerated-block regression detection

## N4. Correction tests

Test:

- PPIE is wrong, independent agent is correct
- PPIE is correct, ElevenLabs is wrong
- Independent reading agent is uncertain
- Acoustic false positives
- Incorrect English alignment
- Wrong IPA candidate
- Failed diacritization attempt
- Context-sensitive dictionary collisions
- New errors caused by a correction
- Repeated unsuccessful correction
- Budget exhaustion
- Unauthorized paid API call prevention
- Recovery from interrupted regeneration

## N5. Language isolation

Run regression tests proving that existing non-Persian language pipelines remain unchanged.

## N6. Case 15

Use the actual approved Case 15 stories when available.

Run semantic alignment and the complete free local review workflow against existing available audio.

Do not create new ElevenLabs audio without authorization.

If the available audio was generated from an unapproved text version, clearly label it incompatible with the approved production source. It may support diagnostics, but must not be silently promoted into approved final audio.

Use real application services and real integrations where safe. Mock paid provider calls to test authorization, failure handling, and request construction.

Do not present mocked results as real acoustic evidence.

---

# O. Iterative Implementation and Optimization

Implement in controlled stages:

**Stage 1 — Architecture audit**

Inspect the actual repository, source versions, adapters, and existing pronunciation components.

**Stage 2 — Source integrity**

Guarantee immutable approved story selection and disable legacy pronunciation influences.

**Stage 3 — Semantic alignment**

Implement English–Persian matching, verification, and audio-block association.

**Stage 4 — Independent expected-reading analysis**

Implement independent linguistic evaluation without trusting PPIE's decisions.

**Stage 5 — Local acoustic preparation**

Implement pitch-preserving analysis copies, sampling, and cleanup.

**Stage 6 — Acoustic review**

Integrate the existing wav2vec2 implementation, candidate comparison, model validation, and uncertainty classification.

**Stage 7 — Autonomous correction**

Implement independent error diagnosis and multiple TTS correction strategies.

**Stage 8 — Controlled regeneration**

Connect correction proposals to the existing ElevenLabs adapter, with explicit cost authorization.

**Stage 9 — Independent post-correction verification**

Ensure audio quality and pronunciation are reassessed after every attempted repair.

**Stage 10 — Frontend integration**

Expose alignment, evidence, correction proposals, audio comparison, and approval controls.

**Stage 11 — Real integration and regression testing**

Run backend, frontend, relevant database/infrastructure tests, and local acoustic evaluation.

**Stage 12 — Optimization and final audit**

Find and resolve implementation defects, improve performance and resource management, remove verified obsolete paths, and repeat the relevant test suites.

At every stage use this engineering loop:

```text
IMPLEMENT
    ↓
TEST
    ↓
IDENTIFY PROBLEMS
    ↓
ANALYZE ROOT CAUSE
    ↓
FIX
    ↓
RETEST
    ↓
REGRESSION CHECK
    ↓
OPTIMIZE
    ↓
CONTINUE
```

Do not leave known failures unexplained.

Distinguish newly introduced failures from previously existing unrelated failures.

Do not weaken tests, disable quality gates, or fabricate successful results to obtain a passing status.

---

# P. Final Acceptance Checklist

Before declaring implementation complete, verify every item:

- [ ] All new pronunciation behavior is isolated to Persian.
- [ ] Approved English and Persian stories are correctly identified.
- [ ] Approved story texts are immutable.
- [ ] Unapproved Voice Prepare derivatives are excluded.
- [ ] Legacy pronunciation dictionaries and old memories cannot influence the run.
- [ ] Every Persian block has semantic alignment or an explicit unresolved state.
- [ ] Many-to-many alignment works.
- [ ] Incorrect semantic matches can be detected.
- [ ] Independent Reading Agent does not blindly trust PPIE.
- [ ] Original ElevenLabs audio at speed 1.2 remains unchanged.
- [ ] Slower review copies are generated locally without another ElevenLabs request.
- [ ] Local audio preparation and acoustic inference incur no speech API charges.
- [ ] The acoustic model's Persian capabilities are benchmarked, not assumed.
- [ ] Every spoken word is considered for review.
- [ ] Short vowels and Ezafe are evaluated where acoustically distinguishable.
- [ ] Uncertain results are never silently classified as correct.
- [ ] Correction Agent independently diagnoses PPIE and ElevenLabs errors.
- [ ] Eleven v4 inline IPA is supported correctly.
- [ ] Run-scoped dictionary rules cannot corrupt unrelated words.
- [ ] Unsupported Eleven v4 SSML phoneme tags are not used.
- [ ] Correction Agent never modifies canonical Persian story text.
- [ ] Unauthorized paid ElevenLabs calls are technically blocked.
- [ ] Only affected blocks are regenerated when appropriate.
- [ ] New recordings are independently reviewed.
- [ ] Regressions prevent automatic replacement.
- [ ] Original recordings remain recoverable.
- [ ] Final audio receives post-speed 1.1.
- [ ] Run-specific pronunciation decisions and alignments are not reused across runs.
- [ ] UI exposes original/corrected audio, evidence, and approval controls.
- [ ] Backend and frontend tests pass, or unresolved failures are documented precisely.
- [ ] Existing non-Persian workflows remain unaffected.
- [ ] No code was pushed or merged without approval.

---

# Q. Required Final Engineering Report

When implementation and available tests are complete, report:

1. Actual architecture changes and files modified.
2. Source approval/version verification results.
3. English–Persian alignment coverage and unresolved cases.
4. Acoustic models evaluated and benchmark results.
5. Review accuracy, including false positives and uncertainty.
6. Identified PPIE-related and ElevenLabs-related failure categories.
7. Correction strategies implemented and verified.
8. Real versus simulated test coverage.
9. Backend, frontend, and regression test results.
10. Performance and resource usage.
11. Cost authorization safeguards.
12. Remaining limitations and production-readiness status.
13. Exact verification that no other languages were changed.

Do not claim production-ready acoustic correction until there is sufficient labeled evidence.

**Final objective:** Build a system that understands what the Persian narrator should say, listens to what ElevenLabs actually says, detects supported pronunciation errors independently, intelligently creates corrected TTS instructions, and verifies that the audio correction genuinely worked—without modifying the approved story, affecting other languages, or spending money without authorization.

Implement the full architecture in the existing application and report completed work and unresolved limitations accurately.
