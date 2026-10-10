# Phase 6 Semantic Classifier Audit

## Production Dependency

The classifier calls the configured generation provider through the existing `consistency_checker` role:

```text
provider: APIMasterGenerationProvider
provider name: apimaster
role: consistency_checker
temperature: 0.0
seed: 17
```

The deterministic strength comparison remains local. The LLM is responsible only for classifying whether the rewrite directly asserts the underlying fact or attributes it to a source.

Production does not silently fall back. If the provider is unavailable or returns invalid output, the call raises. Offline evaluation must explicitly pass `--allow-fallback`; those results are labeled `deterministic_fallback`, logged, and converted to `HUMAN_REVIEW` rather than accepted or rejected.

## Live Run

Command:

```text
PYTHONPATH=. python scripts/evaluate_semantic_compression.py --concurrency 1 --timeout 75 \
  > /tmp/semantic_live_results.json 2> /tmp/semantic_live_progress.log
```

The provider health check returned:

```text
provider apimaster configured True health True
```

The provider's single-call latency was approximately 35 seconds. The completed low-concurrency run used one in-flight request and a 75-second per-case timeout. Progress was emitted before each actual provider call and after each completion. The raw progress log contained:

```text
START: 40
DONE: 40
FALLBACK: 0
```

The output contained 40 per-case records and this aggregate:

```text
total: 40
classifier_sources: [llm:apimaster/gpt-5.6-luna]
accept: 22
reject: 18
human_review: 0
expected_safe: 22
expected_violations: 18
```

Against the labeled set, the confusion counts were:

```text
safe -> ACCEPT: 22
safe -> REJECT: 0
violation -> REJECT: 18
violation -> ACCEPT: 0
HUMAN_REVIEW: 0
```

So this run measured 0% false positives, 0% false negatives, and 0% abstention on this 40-case manual evaluation. The mean classifier confidence was 0.98475 and the minimum was 0.94. The result is a live-provider evaluation, not a claim of exhaustive production accuracy.

## Explicit Offline Fallback Run

Command:

```text
PYTHONPATH=. python scripts/evaluate_semantic_compression.py --allow-fallback
```

Output:

```text
total: 40
classifier_sources: [deterministic_fallback]
accept: 0
reject: 0
human_review: 40
expected_safe: 22
expected_violations: 18
```

The fallback emitted 40 explicit warning records. This is expected: fallback classification is not authorized to make a production gate decision.

## Gate Status

The classifier interface, fixed temperature/seed configuration, deterministic strength layer, explicit fallback logging, and three-way decision model are implemented. The requested 30 accumulated cases plus 10 fresh cases completed through the configured provider at concurrency 1. Phase 6 remains at the review gate; Phase 7 has not started.
