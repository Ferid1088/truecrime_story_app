# Persian pronunciation agents — prompt changelog

## 1.0.1 — 2026-11-03
- Held-out evaluation (n=10, five text agents) found: the QA agent approved an audio claim that had no recording behind it; the memory agent assumed a global scope when none was stated; the QA problem list had no value for a wasteful retry. Fixed in the QA and memory prompts and in the QA schema (`cost_waste`).

## 1.0.0 — 2026-11-03
- First complete prompts and few-shot datasets for the seven agents (linguist, risk, intervention, audio_observer, prosody, memory, qa).
- Every example is `assistant_authored_pending_linguistic_review` or `hypothetical_scenario`; none is owner-verified yet. Examples about audio are
  hypothetical and are never evidence about a recording.
