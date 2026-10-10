# Phase 11: Publishing Plan

## Command

```text
PYTHONPATH=. python scripts/phase11_publish_plan.py
```

No approved/persisted `ShortFormConcept` rows exist for case 6, so the plan
explicitly uses the five Phase 10 verification candidates. The
`index % len(candidates)` assignment in this dry run is a placeholder only: it
exists to give every configured platform slot a visible concept id while the
approved concept table is empty. It is not the production selection algorithm.
Production distribution must derive platform variants from approved concepts,
then choose each concept/platform pairing by platform fit and gate status. This
plan is review-only; it does not publish or persist concepts.

## Real Configuration

```text
youtube_short: 5
instagram_reel: 6
facebook_reel: 5
tiktok_video: 8
total: 24
window: 14 days
```

The generated plan reported:

```text
source: phase10_verification_candidates
persisted_approved_concepts: 0
scheduled_counts: {'facebook_reel': 5, 'instagram_reel': 6, 'tiktok_video': 8, 'youtube_short': 5}
total_slots: 24
days_used: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
max_slots_on_one_day: 2
all_require_human_approval: True
```

Every slot has `status="planned"` and `approved_only=true`. Nothing is
scheduled for publication without later human approval. The plan uses all 14
days and does not dump the placements on day one.

## Verification

```text
PYTHONPATH=. pytest -q tests/test_longform_phase11.py
1 passed, 1 warning in 0.03s
```

Implementation:

```text
app/shortform/operations.py
scripts/phase11_publish_plan.py
tests/test_longform_phase11.py
```

Phase 11 generates a valid review-only plan. Persisted approved short concepts
remain a data gap; the five verification candidates are explicitly labeled as
the source instead.
