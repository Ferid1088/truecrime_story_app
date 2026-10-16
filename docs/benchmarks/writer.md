# Writer benchmark

Numbers and ids only. Deterministic metrics: scripts/writer_baseline.py.

### Baseline: old writer, Persian master v1 of case 5 — case 5, story version 6 (v1, fa, model gemini-3.8-flash, prompt not recorded ) — 2026-10-16

| beat | words | target | length error | sentences | mean len | len variance | short share | repeated trigrams | stock phrases |
|---|---|---|---|---|---|---|---|---|---|
| act1 | 1208 | 1078 | 0.121 | 42 | 28.76 | 205.18 | 0.024 | 0.01 | 0 |
| act2 | 1498 | 1274 | 0.176 | 51 | 29.37 | 226.04 | 0.02 | 0.0247 | 0 |
| act3 | 1717 | 1371 | 0.252 | 59 | 29.1 | 227.07 | 0.034 | 0.0297 | 0 |

Validators' own record for the whole version: grounding score 0.78, unsupported claims 8, engagement score 61.0, failed gates ['grounding', 'consistency', 'engagement_below_threshold']; critic section scores [(None, 50), (None, 60), (None, 66), (None, 58), (None, 65)].

### New writer (claude-sonnet-5-5, rewritten prompt) - Persian master v1 of case 8, 45 min plan — case 8, story version 7 (v1, fa, model claude-sonnet-5-5, prompt agents/story/write_act 29d17858) — 2026-10-16

| beat | words | target | length error | sentences | mean len | len variance | short share | repeated trigrams | stock phrases |
|---|---|---|---|---|---|---|---|---|---|
| act1 | 845 | 836 | 0.011 | 56 | 15.09 | 67.69 | 0.071 | 0.051 | 0 |
| act2 | 903 | 1003 | -0.1 | 61 | 14.8 | 47.99 | 0.082 | 0.0355 | 0 |
| act3 | 1202 | 1338 | -0.102 | 91 | 13.21 | 33.26 | 0.099 | 0.0433 | 0 |
| act4 | 1370 | 1226 | 0.117 | 95 | 14.42 | 57.84 | 0.095 | 0.0446 | 0 |
| act5 | 878 | 836 | 0.05 | 58 | 15.14 | 58.77 | 0.052 | 0.0308 | 0 |
| act6 | 893 | 836 | 0.068 | 70 | 12.76 | 26.16 | 0.114 | 0.0325 | 0 |

Validators' own record for the whole version: grounding score 0.0, unsupported claims 1, engagement score 52.0, failed gates ['grounding', 'engagement_below_threshold']; critic section scores [(None, 67), (None, 67), (None, 64), (None, 68), (None, 58), (None, 63)].

