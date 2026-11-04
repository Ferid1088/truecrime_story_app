# Persian ezafe intelligence

## Where it runs
Final Persian text → reading editor (`reading/writer.py`) + checker (`reading/checker.py`) → arbiter → **ezafe analyzer** (`reading/ezafe.py`, inside
`Planner.plan`) → pronunciation memory → risk engine → adaptive planner → ElevenLabs (`voice_render.py`) → listening QA → correction memory. There is no
second narration pipeline: the renderer's planner calls the analyzer for every block and the risk engine and the planner consume its result.

## What each part does
- **Editor, checker, arbiter** (prompts `prompts/documentary/reading/*_system.md`): the reading of each word carries the ezafe (`-e`/`-ye`). Each agent also
  gives its own judgement `{"present": true|false|null, "relation", "confidence"}`. The checker lists only the words where it holds an ezafe the editor did not
  write, rejects one, or cannot decide. A disagreement about presence goes to the arbiter like any rejected reading. Every judgement is kept with the
  reading (`ReadingWord.ezafe`). Owner answers replace the reading and drop the agents' judgements: human readings take precedence.
- **Analyzer** (deterministic, `config/parameters/documentary.json` → `ezafe`): one boundary per word with the structured output
  (`word, next_word, ezafe_required, ezafe_type NONE|E|YE|UNCERTAIN, final_phoneme, expected_reading, grammatical_relation, confidence, evidence_type,
  requires_tts_intervention`). The analyzer always leaves `requires_tts_intervention` as null; the planner fills it in.
  - **Rules (no ezafe):** the end of the block; digits; punctuation or a reading pause after the word; function words (`never_takes`); before the object marker or a conjunction (`never_before`); before a verb with
    a `می‌`/`نمی‌` prefix.
  - **Written ezafe:** a kasra, a hamza on ه or ۀ, a half-space yeh after ه, or a carrier yeh after ā/u/o.
  - **Ambiguities:** a month before a year in digits, and compound/phrase pairs such as پدر بزرگ. These stay UNCERTAIN unless the text writes the ezafe or the
    agents are sure.
  - **-e or -ye:** decided by the spoken last sound in the reading, never the last letter. A ه said h takes -e (دادگاهِ, ماهِ). A ه said e takes -ye (خانهٔ). After
    ā, u, o or i the ezafe is -ye. After a diphthong or a consonantal و/ی it is -e.
  - **Conflicts:** an agent against the reading, the text against the reading, or a rule against the reading is a conflict, never silently decided.
- **Risk** (`pronunciation_risk.py`):
  - `ezafe_unwritten` (1): the voice has to infer the ezafe.
  - `ezafe_uncertain` (2).
  - `ezafe_conflict` (3).
  - `ezafe_phonology` (2): the reading writes -ye after a consonant.

  A settled ezafe that is written adds nothing, and an ezafe alone is never HIGH.
- **Planner** (`pronunciation_planner.py`):
  - **Escalate:** an UNCERTAIN ezafe, or one disputed without the arbiter's decision, is never written. A HIGH word with such an ezafe is escalated.
  - **`ezafe_mark`:** when the ezafe is the only risk, only the ezafe is written (`ezafe.mark`). A vowel risk still gets `selective_marks`, which now writes the
    ezafe by its sound.
  - **Memory:** a validated ezafe correction is reused only for the same exact form, next word, type (`sense` `ezafe:E` / `ezafe:YE`), relation, voice and model.
- **Memory** (`PronunciationEntry`, no new column): an ezafe candidate is kept with:
  - its `next_word`, `sense` and `sentence`;
  - `grammar.ezafe`: the whole boundary, including relation, type and expected reading;
  - its intervention, with voice and model.

  It becomes reusable only when a person approves it and the intervention is validated. Nothing in this path approves anything.

## Bugs found and fixed while building it
1. **The respelling wrote a hamza on every final ه with an ezafe.** dādgāh-e became دادگاهٔ, which reads "dādgāye".
   - The preview of 2026-11-04 sent this wrong form in two blocks: دادگاهٔ in FA_B40_g03_01 and آزمایِشگاهٔ in FA_B21_g01_01.
   - Now a ه said h takes a kasra. A final i takes a kasra on its yeh (صندلیِ, not صندلیی).
2. **The film table removed the editor's ezafe.** It replaced the whole reading, and 181 words in Case 15's reading cache lost theirs (ru-ye → ru, harfhā-ye →
   harfhā).
   - The table now sets the sounds and keeps the sentence's ezafe.
   - For an old cache, the analyzer trusts the text's written ezafe where only the table removed it.
3. **The planner's own untried candidates counted as "earlier failures"** (+2 each time).
4. **Every word of a long institution's name counted as a name everywhere.** For example, دادگاه counted as a name because of "دادگاه عمومیِ شهرستان میامی".

## Measured
Decision cases are version-controlled in `prompts/pronunciation/decision_cases/`. Errors are counted apart by `ezafe.evaluate`: false positive, false negative,
phonology, uncertainty, relation, evidence and intervention.

| Case set | Cases | Boundaries | Errors (every kind) |
|---|---|---|---|
| `ezafe.json` (demonstrations) | 35 | 61 | 0 |
| `ezafe_heldout.json` (held out, other words) | 18 | 25 | 0 |

Two demonstration expectations were corrected after the first run:
- One was the assistant's own mistake: it expected a rule where the analyzer can only infer.
- One exposed a code bug that was then fixed: a written ezafe hid a month name from the word lists.

The held-out set passed on its first run and was not used to change anything.

## Not established
- **The cases are linguistic expectations written by the assistant.** A native linguist has not reviewed them, and they are not observations of ElevenLabs audio.
- **Grammar is limited.** The analyzer knows only what the reading agents, the text's spelling and the word lists give it. It cannot parse the syntax itself:
  - A noun before a verb without a prefix (خانه رفت) is NONE only because the reading says so.
  - A relation nobody named is `unknown`.
- **No live agent run with the new prompts.** The new reading prompts were not run on a real block, which would be a paid model call. The reading cache of
  Case 15 was written by the old prompts, so it has no agent judgements. The changed prompts invalidate every block's cache key, so the next reading run of a
  film pays for all its blocks again and may raise new doubts for the owner.
- **No audio verification.** Whether the voice says an ezafe mark as intended is not verified. Acoustic verification stays off: no recognizer has passed a
  benchmark on labelled Persian audio.
- **Word lists, weights and confidence values are set by reasoning, not fitted.**
