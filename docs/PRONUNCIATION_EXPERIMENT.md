# Controlled voice experiment — proposal for the owner's approval (NOT run)

Same ElevenLabs voice `I3gMKh0nwZ8NQXKqUg6F`, model `eleven_v4`, settings {"stability": 0.2, "similarity_boost": 0.75, "style": 0.05, "use_speaker_boost": true, "speed": 1.2}, seed 1, for all arms; no listening/ASR retakes (one take per request).

| Arm | What the voice is sent |
|---|---|
| A | the original text, no dictionary, no intervention |
| B | what the pipeline did before the planner: the owner-approved words respelled, plus the film's dictionary |
| C | the risk engine and the adaptive planner (the same dictionary, plus the planner's decisions) |

**Selection** (version 289, language fa; chosen from the planner's own decisions, the shortest block of each kind; 14 blocks, 42 takes):

| Block | Group | Target words | Characters A / B / C | Dictionary in B and C |
|---|---|---|---|---|
| FA_B02_g03_01 | control |  | 81 / 81 / 81 | no |
| FA_B26_g02_01 | control |  | 109 / 109 / 109 | no |
| FA_B49_g04_01 | control |  | 110 / 110 / 110 | no |
| FA_B53_g02_01 | dictionary | کالب ۲۲۹ | 88 / 88 / 88 | yes |
| FA_B15_g02_01 | dictionary | جیل اسمیت | 96 / 96 / 96 | yes |
| FA_B53_g04_01 | dictionary | در قتل مشدد کالب فلین | 99 / 99 / 99 | yes |
| FA_B39_g03_01 | approved | پرت | 116 / 116 / 116 | yes |
| FA_B50_g03_01 | approved | ببخشتش | 126 / 130 / 130 | yes |
| FA_B54_g02_01 | candidate | سلاح | 111 / 111 / 113 | yes |
| FA_B11_g01_01 | candidate | معاون | 112 / 112 / 115 | yes |
| FA_B09_g01_01 | candidate | مرکز | 152 / 152 / 154 | yes |
| FA_B53_g05_01 | candidate | زندان | 155 / 155 / 156 | yes |
| FA_B49_g01_01 | escalated | دادگاه عمومی | 123 / 123 / 123 | yes |
| FA_B41_g01_01 | escalated | عاشقانه | 138 / 138 / 138 | yes |

Control blocks carry the same request in every arm (own folder per arm, so three real calls): their differences are the run-to-run noise of the voice. Any
effect has to exceed it.

**Cost** (price 71.504 EUR per 1M characters, `config/parameters/pricing.json`):
- characters sent: **4864** (text only; previous/next context text is not counted by the provider as far as documented);
- estimated cost: **0.3478 EUR**; worst case if the provider also counted the context text (19165 characters): **1.3704 EUR**;
- proposed hard cap: **2.06 EUR** (worst case x 1.5). `scripts/run_voice_experiment.py` refuses to start without `--confirm-spend`, with a cap below the worst case, or when
  the case lacks room for the cap (Case 15 had spent 199.92 of 300 EUR); every request is counted and the one that would pass the cap is not sent.
- the audio goes to `data/experiments/<name>/<arm>/`; the film's narration folder is not touched.

**What it measures**: pronunciation of the target words (their clips are registered in the phonetic benchmark as unlabeled clips; a person labels them without seeing the arm,
the arm of each clip is only in `blind_key.json`); prosody features (speech/pause timing, energy, coarse pitch) of every block against arm A, counted as a difference only
beyond twice the control blocks' noise; the actual cost from the case ledger.
**What it cannot show**: with one take per arm and a few target words the result is an indication, not statistics; naturalness by ear still needs a blind listening by the owner.

Command after approval: `.venv/bin/python scripts/run_voice_experiment.py --version 289 --name planner-ab-1 --cap-eur 2.06 --confirm-spend`
