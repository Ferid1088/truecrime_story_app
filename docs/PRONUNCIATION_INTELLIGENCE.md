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
