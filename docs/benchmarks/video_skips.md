# Video skips: case 8 "Lizzie Borden axe murders"

Ids and numbers only. Read from `video_sources` of case 8 (live PostgreSQL), 2026-10-16.

## Baseline before the change (job 32, caps in place)

32 videos discovered: 15 processed, 6 no transcript, 1 failed, 10 skipped. Skipped by reason:
`per_language_cap` 10, `consecutive_low_value` 0, `max_transcripts_per_case` 0, `below_min_relevance` 0.
Every one of the 10 had relevance_score 0.75 (one 0.5): the keyword score cannot tell them apart.

Independent reviewer (separate agent, title and channel only; the skipped rows stored no description):

| video_source id | relevance score | skip reason | reviewer: relevant | reviewer reason |
|---|---|---|---|---|
| 9 | 0.75 | per_language_cap | yes | biography documentary on the real case |
| 10 | 0.75 | per_language_cap | no | "full episode", title alone does not confirm it is factual |
| 11 | 0.75 | per_language_cap | yes | examines guilt in the real case |
| 12 | 0.75 | per_language_cap | yes | public-broadcaster segment on the case |
| 13 | 0.75 | per_language_cap | yes | addresses the murders and guilt |
| 14 | 0.75 | per_language_cap | yes | explainer on guilt and evidence |
| 15 | 0.75 | per_language_cap | no | clickbait about the house |
| 16 | 0.75 | per_language_cap | no | overnight haunting stay, entertainment |
| 17 | 0.75 | per_language_cap | yes | podcast episode on the murders |
| 18 | 0.50 | per_language_cap | yes | news feature on the case |

Result: 7 of 10 skipped videos were judged relevant, all lost to a count cap, none to relevance.
Lowest relevance score of a relevant video: 0.50. Threshold decision: below 0.25 clearly
unrelated (skipped with `unrelated_to_case`), 0.25 to below 0.50 decided by a cheap model reading
title, channel and description, 0.50 and above accepted. (Config: `youtube_research.relevance`.)

## After the change (job 35, no count caps, no novelty stop)

47 videos discovered (more stems per language, up to 20 results per query, no list cap).
38 processed, 8 relevant but no transcript available (status `unavailable`, shown as "relevant, no
transcript available"), 1 failed, **0 skipped**. The 10 earlier skips were all processed.
Not measured: how many of the 15 new videos were relevant (no reviewer pass on the new set).
