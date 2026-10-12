# Part 2 research-root proof

The persistent SQLite template was rebuilt from a `from_zero` job. Its first
stage is the real `research` stage in `app/documentary/jobs.py:241-247`; the
search/fetch boundary is replaced only by the input-sensitive test provider in
`tests/test_visual_production.py:943-1048`. The resulting rows are ingested by
`app/services/research_jobs.py:366-541`; the template contains eight
`research_results` and eight full-text `sources` rows.

The research-result perturbation was run with:

```text
PYTHONPATH=.:tests .venv/bin/python - <<'PY'
from run_soundness_phase5 import run_case
...
PY
```

Evidence is committed in `tests/golden/soundness_research_result.json` and the
generated matrix. The run restored the template in 0.25 s and completed in
44.477 s. It changed exactly `research_result:shared`; the registry closure
predicted the full downstream set, so `actual_changed ⊆ predicted_changed` and
there were no hidden inputs. The generated status now has a passing research
record; all artifact types are SOUND only because the existing Phase 4/5
snapshot-diff records also pass.
