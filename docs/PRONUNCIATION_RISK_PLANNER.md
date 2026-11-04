# Pronunciation risk engine and adaptive TTS planner — decision specification v1.0.0

Both components are **deterministic** (no model call). Their decisions are specified by this document, by the weights in
`config/parameters/documentary.json` → `pronunciation_risk`, and by executable examples in `prompts/pronunciation/decision_cases/`
(`risk.json` 13 cases, `planner.json` 12 cases; `*_heldout.json` are never used to tune anything). `tests/test_pronunciation_planner.py` runs them.

## Position in the pipeline
Original text → Reading intelligence (`reading/`: editor, checker, arbiter) → **Ezafe analyzer** (`reading/ezafe.py`, see `docs/PERSIAN_EZAFE.md`) → Pronunciation memory (`pronunciation_memory.py`) → **Risk engine** (`pronunciation_risk.py`) →
**Planner** (`pronunciation_planner.py`) → ElevenLabs (`voice_render.py`, the text and the block dictionary of each take) → listening QA (reading loop) →
bounded correction loop (`reading/loop.py`, `reading.max_rounds`) → `Planner.learn` (evidence and intervention outcomes, never approvals) → the next risk assessment.

## Risk
Score = sum of signal weights. LOW < `medium_from` ≤ MEDIUM < `high_from` ≤ HIGH < `critical_from` ≤ CRITICAL. Every positive signal carries its evidence text;
HIGH and CRITICAL always list them. `verification` is `human_approved`/`auto_verified` only when the memory holds an approval of this exact written form; LOW is
`unverified` like the rest.

Signals: reading doubt (checker/arbiter), editor confidence low/medium, film-table override, name/foreign (people, foreign words, one-word places only), ezafe (only an unwritten, uncertain or disputed one, or a reading whose ezafe form its own final sound does not take: `ezafe_unwritten`, `ezafe_uncertain`, `ezafe_conflict`, `ezafe_phonology`), colloquial ending, hazard letters (ع ء ئ ؤ ة unmarked),
final consonant h, earlier failure in the memory (an entry an earlier system or a person made, or one with an observed wrong form: the planner's own untried candidates do not count — fixed 2026-11-04, they made every word the planner once touched riskier by itself), failed interventions, a listening model that heard it wrong, acoustic/human mismatch or
match (including a person's label of a benchmark clip). History counts only for the same voice and model (or entries without one).

## Planner (first match wins, per word)
1. a number → keep (read by code);
2. a reading a person approved or the reading loop verified (`Voicing.fixed`) → respelling (Persian letters with marks; unchanged behaviour);
3. a **validated** intervention for this exact written form, voice, model and context → dictionary rule (only if the language has a dictionary) or respelling;
4. an **approved** memory entry (human or app) matching the exact form and context (prev/next word, voice, model) → its approved pronunciation;
5. HIGH/CRITICAL word already covered by a rule of the language's dictionary → keep (nothing is added on top);
6. LOW/MEDIUM → keep (MEDIUM is left to the listening QA);
6a. HIGH/CRITICAL whose ezafe is UNCERTAIN, or disputed without an arbiter's decision → `escalate`; HIGH/CRITICAL whose risk is its ezafe alone (the score without the
   ezafe signals is LOW/MEDIUM) → `ezafe_mark`: only the ezafe written on the word, a candidate kept in the memory with its next word, type and relation;
7. HIGH/CRITICAL: the word's own letters with the reading's short vowels as marks, **only if** the consonant skeleton is identical (otherwise nothing is invented) →
   `selective_marks`, a candidate (not validated; CRITICAL also `needs_review`); else `escalate` (text unchanged, reported once per word).
A block may carry at most `max_changed_fraction` unvalidated changes and never more than `max_changed_in_a_row` in a row. No sentence is ever diacritized as a whole.
Final h (audited 2026-11-04): no rule turns ه into ح. The respelling writes a final consonant h with **the word's own letter**: ح for a word written with ح
(مسلح, سلاح: this preserves the word, it converts nothing), ه for a word written with ه (دادگاه, شاه), and ح for a word written with ه only if `reading.final_h_evidence`
lists that word with evidence (it is empty: no word has it). The earlier bug (مسلح said "mosallal") was the respelling replacing the word's own ح by ه. Not
verified by ear: that keeping ح makes the voice say "mosallah" (it is in the controlled experiment's candidate/dictionary blocks only if the word occurs there).

## Learning
`Planner.learn` books a failure as evidence (`model`/mismatch) and against the intervention (two failures → `failed`); a success is evidence only unless
`validate_from_listener` is true (default false: a listening model does not validate). Nothing in this path approves an entry; legacy and human-approved entries are never changed.

## What is not established
Weights and thresholds are set by reasoning, not fitted: there are no human-labelled clips yet. Short-vowel ambiguity is represented only by the hazard letters and the
disagreement signals (no word-frequency list exists). Naturalness is measured on the decisions (how much was changed), not on the audio; listening naturalness QA and
prosody verification are not built. No render with the planner has been made (a render needs the owner's go).
