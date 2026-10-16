# Writer benchmark

Numbers and ids only. Deterministic metrics: scripts/writer_baseline.py.

### Baseline: old writer, Persian master v1 of case 5 — case 5, story version 6 (v1, fa, model gemini-3.8-flash, prompt not recorded ) — 2026-10-16

| beat | words | target | length error | sentences | mean len | len variance | short share | repeated trigrams | stock phrases |
|---|---|---|---|---|---|---|---|---|---|
| act1 | 1208 | 1078 | 0.121 | 42 | 28.76 | 205.18 | 0.024 | 0.01 | 0 |
| act2 | 1498 | 1274 | 0.176 | 51 | 29.37 | 226.04 | 0.02 | 0.0247 | 0 |
| act3 | 1717 | 1371 | 0.252 | 59 | 29.1 | 227.07 | 0.034 | 0.0297 | 0 |

Validators' own record for the whole version: grounding score 0.78, unsupported claims 8, engagement score 61.0, failed gates ['grounding', 'consistency', 'engagement_below_threshold']; critic section scores [(None, 50), (None, 60), (None, 66), (None, 58), (None, 65)].

