# Persian pronunciation intelligence system — state (2026-11-03)

Built and verified (tests, and live where stated):
- **Pronunciation memory** (`app/documentary/pronunciation_memory.py`, migrations 0086 and 0087, verified live on PostgreSQL after a backup and a rehearsal
  on a restored copy): entries by exact written form and context; a person's approval is never overwritten by an agent; a different human approval needs
  `supersede`; every state is a version and can be rolled back; interventions are validated by what the voice did and are kept apart from the approved
  pronunciation; evidence is typed (acoustic, asr, model, human, inference); an append-only audit log.
- **Legacy import** (`app/documentary/pronunciation_import.py`): the owner's reading approvals and the learned respellings are `legacy_unverified` (71 entries
  from 74 rows); none is a reference until a person approves it in the review.
- **Review page** (React tab "Pronunciation", API under `/api/documentary/pronunciation`): entries by status, the word in the narration with synchronized
  playback, word highlight, waveform, loop, editable marks and IPA, explicit approve, reject and mark uncertain (a named reviewer is required).
- **Seven agents** (`prompts/pronunciation`, `app/documentary/pronunciation_agents.py`): complete versioned prompts, strict output contracts, 8 training and
  2 held-out few-shot examples each (assistant-authored, pending the owner's linguistic review; audio examples are hypothetical).
- **Whisper Persian v4** (`scripts/convert_whisper_persian.py`, `scripts/benchmark_whisper_persian.py`): converted to int8 for faster-whisper and benchmarked on this
  machine against the project's current Persian ASR (see below).
- **Dictionary check and trace** (`app/documentary/pronunciation_trace.py`) from the earlier phase.

Whisper benchmark (12 random Case 15 blocks of 8-30 s, M1 Pro 16 GB, CPU int8, word error rate against the script):

| model | peak RAM | real-time factor | mean WER | blocks within the 20% limit |
|---|---|---|---|---|
| large-v3-turbo (current) | 2.0 GB | 0.34 | 6.2% | 12 of 12 |
| whisper-persian-v4, beam 5 | 3.0 GB | 1.17 | 14.4% | 9 of 12 |
| whisper-persian-v4, greedy | 2.6 GB | 0.80 | 12.9% | 11 of 12 |

Persian v4 writes formal Persian for colloquial speech (خونواده → خانواده, می‌خواستن → می‌خواستند, رو → را), which hides the spoken forms the voice produced and
counts as errors against a colloquial script. It is not a better checker for this narration; it is usable as a second opinion.

Not built yet: phonetic verification layer with word alignment (the existing wav2vec2 phoneme scorer cannot tell [æ] from /e/), the risk engine and adaptive
planner wired into the render, the prosody check on real audio, the retry engine, the controlled comparison on Case 15 blocks (billable), and the real-audio
evaluation of the audio agents.
