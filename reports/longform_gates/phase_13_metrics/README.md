# Phase 13: Metrics and Funnel Tracking

## Mock Persistence

Command:

```text
PYTHONPATH=. python scripts/phase13_seed_mock_metrics.py
```

Output:

```text
inserted: 15
model: phase13_mock
case_id: 6
attribution_status: unavailable
estimated_conversion_rate_values: []
```

The rows cover five case-6 verification concept types across three platforms.
They are explicitly marked `model=phase13_mock`; they are not claimed to be
real platform analytics.

## Persisted Database Evidence

```text
rows: 15
attribution: ['unavailable']
conversion_values: [None]
```

## API Output

Command:

```text
python - <<'PY'
from fastapi.testclient import TestClient
from app.main import app
r=TestClient(app).get('/api/short-form/metrics?case_id=6')
print('status', r.status_code)
print(r.text)
PY
```

Output:

```text
status 200
case_id: 6
comparison_basis: concept_type
metric_rows: 15
concept_types: appeal-turn, dispatch-audio-question, document-drop, verdict-countdown, weather-cold-open
attribution_status: unavailable
```

The full response contains one aggregate per concept type, total views,
platform names, and average completion rate. It does not expose a conversion
number or conversion rate.

## UI and Playwright

The Performance tab calls the metrics API and compares concept types. Platform
names remain context inside each concept-type row; the UI does not rank raw
cross-platform views as if they were directly comparable. It displays
`Attribution: unavailable` and explicitly states that conversion is not shown.

Command:

```text
npx playwright test e2e/phase12-shortform.spec.ts e2e/phase13-metrics.spec.ts
```

Output:

```text
Running 4 tests using 1 worker
4 passed (5.4s)
```

## Backend Tests and Build

```text
PYTHONPATH=. pytest -q tests/test_longform_phase13.py
1 passed, 1 warning in 0.03s

PYTHONPATH=. pytest -q tests/test_shortform_operations.py
5 passed, 1 warning in 0.02s

npm run lint && npm run build
0 lint errors, 1 pre-existing warning in e2e/smoke-real.mjs
TypeScript passed
14/14 static pages generated
```

Implementation:

```text
app/shortform/metrics.py
app/shortform/api.py
scripts/phase13_seed_mock_metrics.py
tests/test_longform_phase13.py
frontend/app/short-form/page.tsx
frontend/lib/api.ts
frontend/e2e/phase13-metrics.spec.ts
```

The remaining limitation is intentional: the 15 persisted records are mocked
for funnel/UI verification, and no real platform attribution or conversion
measurement exists yet. No fake conversion numbers are stored, returned, or
displayed.
