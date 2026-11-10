# PPIE (Persian/Arabic pronunciation system): removed on 2026-11-10

The automatic pronunciation system for Persian (fa) and Arabic (ar) was removed completely on the owner's decision ("everything pronunciation").

## Why

The owner listens to the Persian voice blocks and rebuilds them by hand in the voice review, so an automatic reading of every word is not needed.

## What was removed

- The reading stage (`reading:<lang>`): the reading writer, checker, arbiter, glossary, chooser, risk gate, Tashkeel (Arabic vowel marks), the second text (irab) and the ezafe analyzer.
- The pronunciation planner family: risk engine, planner, explain, trace of the film dictionary, memory, auto-verification, import, review, the phonetic benchmark and the voice experiment.
- The pronunciation key and the phoneme listening loop (`pronunciation.py`), which served only fa and ar (config `pronunciation.languages`), and the film dictionary preparation (`voice_prepare.py`, config `voice.prepared_languages`, which was already empty).
- The API routes, the Pronunciation tab views (entries, plan, conflicts, benchmark, preview), the Reading Review tab, the scripts, prompts, config sections (`ppie`, `reading`, `pronunciation_risk`, `irab`, `ezafe`, `morphology`, `homographs`, `number_reading`, `pronunciation_auto`, `pronunciation_trace`, `voice_prepare`, `phoneme_alignment`, `prosody`, `pronunciation`) and the agents and roles that served them.
- The tables (migration `0097`): `reading_resolutions`, `reading_approvals`, `reading_doubts`, `pronunciation_lexicon`, `pronunciation_entries`, `pronunciation_entry_versions`, `pronunciation_interventions`, `pronunciation_evidence`, `pronunciation_audit_log`, `phonetic_samples`, `phonetic_labels`. All were empty on the live database when they were dropped.

## What replaced it

- Numbers are read by the deterministic number layers in `app/documentary/spoken_numbers/` (fa, ar, and every other language); Arabic and Persian use the same generic path (`reader_for`) as German or Turkish.
- Persian blocks are checked by the semantic audio review (a model listens) and by the owner in the voice review, which stays: listen, correct, rebuild a single block (the `Voice Review` tab, `app/documentary/block_review/`).
- The Whisper speech-to-text check with retakes stays for the languages that are not semantically reviewed.

`tests/test_pronunciation_stack_removed.py` fails if any of it comes back.
