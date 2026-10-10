# Phase 7: Long-Form Rollout Readiness

## Command

```text
PYTHONPATH=. python scripts/report_longform_readiness.py
```

The command is read-only. It inspects every persisted case and reports whether
the case has the source material and validated artifacts required to proceed.
It does not manufacture blueprints, graphs, contracts, or assets.

## Real Database Output

```text
case_id | status | blueprints | stories | visual_assets | media_segments | validated_graphs | contracts
1 | NOT_CANDIDATE | 0 | 6 | 0 | 0 | 0 | 0
  - no EditorialBlueprint exists
  - no VisualAsset rows exist
2 | NOT_CANDIDATE | 0 | 7 | 0 | 0 | 0 | 0
  - no EditorialBlueprint exists
  - no VisualAsset rows exist
3 | NOT_CANDIDATE | 0 | 0 | 0 | 0 | 0 | 0
  - no EditorialBlueprint exists
  - no VisualAsset rows exist
  - no StoryVersion exists
4 | NOT_CANDIDATE | 0 | 1 | 0 | 0 | 0 | 0
  - no EditorialBlueprint exists
  - no VisualAsset rows exist
5 | NOT_CANDIDATE | 0 | 0 | 0 | 0 | 0 | 0
  - no EditorialBlueprint exists
  - no VisualAsset rows exist
  - no StoryVersion exists
6 | READY | 1 | 4 | 32 | 0 | 1 | 1
  - no OriginalMediaSegment rows; audio tagging has no source rows
```

Cases 1–5 are explicitly not candidates for this rollout. They have no
EditorialBlueprint and no VisualAsset rows. Cases 3 and 5 also have no
StoryVersion. They need source preparation and blueprint/asset work before the
RevealGraph and EpistemicContracts pipeline can run.

Case 6 is the only current candidate and is now `READY`: its validated
RevealGraph and approved 330-claim EpistemicContractSet both exist. It has 32
VisualAsset rows and zero OriginalMediaSegment rows, so no audio reveal
tagging can be reported yet.

## Operational Dependency

```text
semantic_classifier_provider: apimaster
semantic_classifier_configured: True
semantic_classifier_role: consistency_checker
semantic_classifier_temperature: 0.0
semantic_classifier_expected_latency: approximately 35 seconds per call
ci_recommendation: use explicit deterministic fallback for offline regression; reserve live API calls for integration verification
```

The semantic compression checker is therefore not a zero-cost local check in
production. Phase 8 must account for API cost, roughly 35 seconds per live
classification call, provider availability, and nondeterministic service
failures even with temperature 0 and a fixed seed. Offline CI/regression runs
should use the explicitly logged deterministic fallback, which returns
`HUMAN_REVIEW` rather than silently accepting or rejecting a rewrite. Live API
calls belong in a separately controlled integration verification job.

## Tooling and Test

Implementation:

```text
app/longform/rollout.py
scripts/report_longform_readiness.py
tests/test_longform_phase7.py
```

Verification command:

```text
PYTHONPATH=. pytest -q tests/test_longform_phase7.py tests/test_longform_phase6.py
```

Output:

```text
2 passed, 1 warning in 0.06s
```

Phase 7 stops at the review gate. Phase 8 has not started.
