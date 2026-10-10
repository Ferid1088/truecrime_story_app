# Phase 6: Case 6 Compression-Safety Review

- Source: persisted `EpistemicContractSet.id=1`, case 6, `StoryVersion.id=15`
- Source claims evaluated: 330
- Labeled rewrites: **660**
- Expected modality violations: **61**
- Expected safe rewrites: **599**
- True positives: 61
- True negatives: 599
- False positives: **0**, rate **0.0%**
- False negatives: **0**, rate **0.0%**

The evaluation set uses every real persisted claim twice: one modality-preserving rewrite and one labeled adversarial rewrite. For `ALLEGED` and `DISPUTED` claims, the adversarial form explicitly reframes the real proposition as established. Safe rewrites preserve the relevant attribution, dispute, or absence-of-evidence signal.

## Required Adversarial Check

### S15_C009_02
- Source claim: `I’m not alleged, sir, I did it.`
- Source modality: `ALLEGED`
- Rewrite: `I did it.`
- Inferred rewrite modality: `ESTABLISHED`
- Result: **FAIL**, correctly flagged as `ALLEGED -> ESTABLISHED`

## Required Safe Paraphrase

### S15_C080
- Source claim: `The strike inflicted a two-inch chest wound that proved rapidly fatal.`
- Source modality: `ESTABLISHED`
- Rewrite: `The record establishes that the strike caused a rapidly fatal two-inch chest wound.`
- Inferred rewrite modality: `ESTABLISHED`
- Result: **PASS**

## Real Corpus Command

```text
PYTHONPATH=. python scripts/evaluate_compression_safety.py
```

Output:

```text
{
  "total": 660,
  "expected_violations": 61,
  "expected_safe": 599,
  "true_positive": 61,
  "true_negative": 599,
  "false_positive": 0,
  "false_negative": 0,
  "false_positive_rate": 0.0,
  "false_negative_rate": 0.0
}
```

This checker is deterministic and reviewable. It infers the rewrite modality from attribution and evidentiary language, then rejects only upward modality changes according to the shared strength order. It does not approve editorial truth; it blocks strengthening for human review.
