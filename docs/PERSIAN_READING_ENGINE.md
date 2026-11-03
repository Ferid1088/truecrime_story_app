# Persian Reading Engine

Runs before the Pronunciation Intelligence Engine (PPIE). It turns the writer's Latin readings into readings with a **status**. The Persian text, the
word numbers and their order are never changed: every reading stays at the number of its word.

```
Persian text → numbered words (words.py) → transcription check (transcription.py)
            → lexical evidence through the morphology (evidence.py, morphology.py, exceptions.py)
            → audit: supported / vowel_conflict / form_conflict / no_evidence (audit.py)
            → contextual selection by a model that did not write the reading (selector.py)
            → readings with status (engine.py) → PPIE
```

## Statuses

| status | meaning |
|---|---|
| verified | the reading is among what the evidence allows (or a verified exception) |
| resolved | the evidence or an exception allows other readings; the sentence's meaning and grammar picked one; it replaces the writer's |
| uncertain | the evidence and the writer disagree and the sentence does not settle it, or the model is not sure; the writer's reading is kept and flagged |
| unverified | no evidence exists (names, present-tense verbs, new words); the writer's reading is kept and never promoted |

## Evidence is not ground truth
* **Source:** English Wiktionary (CC BY-SA 4.0) through the Kaikki.org extract; entries keep their page URL and tags. Community-edited, lemmas and a few
  forms only, its IPA is narrow and noisy. Built by `scripts/build_persian_lexicon.py` into `data/lexicon/` (not committed). Attribution string: `reading.evidence.attribution`.
* Other dialects and classical romanisations (Dari, Kabuli, Classical-Persian, macron letters) are not used. Romanisations are preferred; IPA only fills a gap or when tagged Tehrani.
* **Morphology** (`reading.morphology`): suffixes, clitics, plural, spoken forms of a stem (-ān → -un), host words with a clitic (به+م = beham: a grammar rule, basis `rule`), verbs
  from the infinitive of the lexicon (past stems, past participles; present stems are not analysed).
* **Verified exceptions** (`config/reading_exceptions.json`): readings a person confirmed. Never written by a model. Each is a regression test. Add one with
  `scripts/confirm_reading.py SURFACE RIGHT [WRONG ...] --by "who, when, where"`.

## Transcription (`reading.transcription`)
One scheme. A **format** fault (q for gh, a curly apostrophe, a capital, a Cyrillic look-alike, a space or hyphen that only joins the parts of one word) is rewritten and
recorded in `Checked.fixes`. A **sound doubt** (ē, ō, a lone w) is never rewritten: it is reported. A valid reading can still be the wrong word.

## Coverage is not accuracy
`audit.summary()` reports `coverage` (words with evidence) and `agreement_with_evidence` (of those, readings the evidence allows) apart. Neither is the
accuracy of the readings: the only measure of that is a human-labelled sample, which the exceptions file starts.

## Commands (none calls ElevenLabs)
* `python scripts/build_persian_lexicon.py [raw.jsonl]` — download and index the lexicon.
* `python scripts/audit_readings.py CASE VERSION [LANG] OUT.json` — free audit of a film's readings.
* `python scripts/run_reading_engine.py CASE VERSION LANG OUT.json` — the engine with the live selector; writes only OUT.json and the selection cache.

## Known limits
Present-tense verbs, names without a lexicon entry and colloquial forms the grammar does not derive (اومده → umade) have no evidence. The selector is a model: its
choice is accepted only when it is a lexicon candidate and the model is sure. Wiktionary's own entries can be wrong.
