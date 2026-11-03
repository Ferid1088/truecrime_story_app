# Persian pronunciation agents — prompt changelog

## 1.1.0 — 2026-11-03
- Whisper removed from the pronunciation system. The audio observer is given the output of an acoustic phoneme recognizer (IPA with its own confidence) instead of a transcript; evidence kind `asr` became `phonetic`. A forced alignment of the expected phonemes is explicitly not evidence (aud-004 teaches it).
- Autonomous verification: no agreement after three attempts is `unresolved`, never approved; an agreed entry is `auto_verified`, never `human_approved`.

## 1.0.1 — 2026-11-03
- Held-out evaluation (n=10, five text agents) found: the QA agent approved an audio claim that had no recording behind it; the memory agent assumed a global scope when none was stated; the QA problem list had no value for a wasteful retry. Fixed in the QA and memory prompts and in the QA schema (`cost_waste`).

## 1.0.0 — 2026-11-03
- First complete prompts and few-shot datasets for the seven agents (linguist, risk, intervention, audio_observer, prosody, memory, qa).
- Every example is `assistant_authored_pending_linguistic_review` or `hypothetical_scenario`; none is owner-verified yet. Examples about audio are
  hypothetical and are never evidence about a recording.
