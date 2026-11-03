# Persian pronunciation intelligence system — state (2026-11-03)

Architecture: audio-first. What the voice really said is established by an acoustic phoneme recognizer and by listening, never by a transcript. **Whisper
was removed from this system** (its conversion and benchmark scripts, the converted model and its download are deleted; the film's own ASR check
`app/documentary/asr.py` is a different feature and stays). A forced alignment of expected phonemes onto audio is not evidence of what was said.

Five separate representations: ORIGINAL_TEXT (the script, never changed), REFERENCE_IRAB (the linguist agent's expected pronunciation), TTS_INPUT (the
text and rules actually sent, recorded by `pronunciation_trace.py`), OBSERVED_PHONEMES (what a recognizer heard, with its confidence), QA_RESULT.

Built and verified (tests; live where stated):
- **Pronunciation memory** (`pronunciation_memory.py`, migrations 0086 and 0087, verified live on PostgreSQL after a backup and a rehearsal on a restored
  copy): entries by exact written form and context; a person's approval is never overwritten; versions with rollback; interventions validated by what the
  voice did, apart from the approved pronunciation; typed evidence (acoustic, phonetic, model, human, inference); an append-only audit log.
- **Legacy import**: 71 `legacy_unverified` entries (from 74 rows) with their sources. None is a reference until a person approves it or the app verifies it.
- **Autonomous verification is OFF** (`config pronunciation_auto.enabled`): it is switched on only after a recognizer passes an independent evaluation on
  recordings a native speaker labelled. When on: at most three attempts; agreement gives `auto_verified` (below a person's approval); no agreement leaves the
  entry `unresolved`, never approved. On 2026-11-03 the app approved all 71 entries by linguistic agreement alone and accepted 13 after three attempts; that was
  reverted the same day (`scripts/revert_auto_approvals.py`; the audit log keeps both steps).
- **Review page** (React tab "Pronunciation"): entries by status, the word in the narration with synchronized playback, word highlight, waveform, loop, editable
  marks and IPA, explicit approve, reject, mark uncertain.
- **Seven agents** (`prompts/pronunciation`, `app/documentary/pronunciation_agents.py`): versioned prompts (1.1.0), strict contracts, 8 training and 2 held-out
  examples each (assistant-authored, pending linguistic review; audio examples hypothetical). The audio observer is given recognizer output, not a transcript.

Phoneme recognizer evaluation (`scripts/benchmark_phoneme_recognizers.py`, labels in `data/phonetic_benchmark/labels.json`). The only labelled recordings that exist
are 4 ElevenLabs clips of one word (عهدهایی), labelled by the owner by ear (one listener): too few for error rates. Each recognizer's own output is compared with the two
candidate pronunciations (ahd, ohd) of the word; this is not a forced alignment.

| recognizer | clips called right | ties | peak RAM | decode time for 4 clips |
|---|---|---|---|---|
| Allosaurus (Persian inventory) | 0 of 4 | 3 | 0.44 GB | 0.7 s |
| wav2vec2 espeak (xlsr-53) | 3 of 4 | 1 | 1.8 GB | 3.7 s |
| Allophant | not evaluated: its installation failed in this environment | | | |

No recognizer has passed. wav2vec2 leans the right way on 3 of 4 clips but by one edit only; Allosaurus does not separate the two pronunciations. No model is selected and
autonomous approval stays off. What is missing is a labelled reference set (native-speaker recordings of minimal pairs: æ/e/o, ezafe, colloquial forms, names,
several speeds and voices).

Not built yet: word alignment of observed phonemes (the recognizer's timestamps are not word boundaries), the correction loop (three attempts, then unresolved), the
prosody check on real audio, the risk engine and planner wired into the render, the evaluation of the audio agents on real recordings.

## Phonetic benchmark and labelling (2026-11-03)

**What exists (verified live unless noted):**
- Tables `phonetic_samples` / `phonetic_labels` (migration 0088, applied on live PostgreSQL after a verified backup and a rehearsed upgrade/downgrade/upgrade).
- `scripts/extract_phonetic_samples.py` cuts words from the existing Case 15 narration (v289) into 16 kHz mono clips. No audio is generated, nothing is billed. 234 clips now exist: short vowels 140, ezafe 34, colloquial 30, names 30. All from ONE voice at the film's speed.
- Candidates per clip come from the reading cache: the expected reading plus one-vowel variants, or the word with and without its ezafe.
- Opinions are kept as they are, per actor and kind (`agent`, `recognizer`, `human`). Status: `unlabeled` → `agent_consensus` (two or more non-human opinions agree; NOT ground truth) / `disputed` / `unclear`; only a named person's label gives `human_verified`.
- `scripts/propose_phonetic_labels.py`: two shuffled listening-agent rounds plus the wav2vec2 recognizer on clips without opinions. 40 clips done: 25 agent_consensus, 15 disputed. Cost: about EUR 0.06 (booked at an upper bound, the model has no price entry).
- Pronunciation tab → "Phonetic benchmark": clips (disputed first), playback, waveform, candidate buttons, "cannot tell", opinion table, in-page recording (MediaRecorder) and file upload, report.

**What is not established:**
- No clip has a human label yet, so there is no measure of the agents or any recognizer. Agent consensus can be wrong (the expected reading is always among the options, which may bias the agents toward it).
- Speed and voice diversity need recordings or other voices; the clips so far have one.
- No recognizer is chosen. Alignment, risk engine, adaptive TTS planner, prosody verification and the correction loop are not built: they are gated on at least 60 human-verified clips (`enough_for_a_verdict`), and `pronunciation_auto.enabled` stays false.

## Risk engine and adaptive planner (2026-11-03, correction of the architecture)

Added between the memory and ElevenLabs, as the specification demands: `pronunciation_risk.py`, `pronunciation_planner.py`, specification in
`docs/PRONUNCIATION_RISK_PLANNER.md`, executable cases in `prompts/pronunciation/decision_cases/`. Connected in `voice_render.py` (the text and the block dictionary of
every take come from the plan; `pronunciation_plan.json` is written next to the narration), `pipeline/language.py` (planner built with the language's voice and model)
and `reading/loop.py` (what the listening found goes to `Planner.learn`).

Dry run on Case 15 v289 (reading cache + database, no model, no provider, nothing written): 8370 words; LOW 6838, MEDIUM 936, HIGH 592, CRITICAL 4;
decisions: keep 8273, respelling 46 (existing owner approvals), selective marks 16 (candidates), escalate 35 (14 distinct words, e.g. دادگاه ×17); 0.74 % of the words
changed, at most 9.1 % of any block, longest run 1. NOT done: a render with the planner (needs the owner's go); acoustic proof that candidates are obeyed; naturalness by
listening; weights fitted to evidence.
