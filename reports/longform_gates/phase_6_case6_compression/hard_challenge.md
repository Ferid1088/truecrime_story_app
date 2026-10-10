# Phase 6 Hard Adversarial Challenge

This is a separate 10-case set, not part of the mechanically generated 660-case baseline.

## How the 660-case baseline was generated

For every one of the 330 real persisted case-6 claims, the evaluator generated two rewrites:

- Safe rewrite: a template preserving the source modality, such as `According to the reported account, ...`, `The disputed account remains contested: ...`, or `The record notes an absence of evidence: ...`.
- Violation rewrite: for `ALLEGED` and `DISPUTED`, the real claim text was placed after `It is established that ...`; quoted confessions had the introductory “I’m not alleged” wording removed. This was mechanical, not human-authored.

Therefore the earlier 0%/0% result measured consistency against templates, not natural editorial rewrites.

## Natural-Language Challenge Results

### Strengthened violations

All five were real case-6 propositions rewritten with different wording rather than simply deleting a hedge.

| Source | Rewrite | Expected | Inferred | Result |
|---|---|---|---|---|
| S15_C009_02 `ALLEGED` | Anthony was the killer. | violation | ESTABLISHED | correctly rejected |
| S15_C070_02 `ALLEGED` | Metcalf initiated the physical confrontation. | violation | ESTABLISHED | correctly rejected |
| S15_C101_02 `ALLEGED` | The stabbing was a justified act of self-defense. | violation | ESTABLISHED | correctly rejected |
| S15_C164_02 `ALLEGED` | Prosecutors breached the off-record character-evidence agreement. | violation | ESTABLISHED | correctly rejected |
| S15_C167_02 `DISPUTED` | The agreement categorically excluded defendants who testified. | violation | ALLEGED | correctly rejected |

Results: 5/5 true positives, 0/5 false negatives, false-negative rate 0.0% on this challenge subset.

### Aggressively compressed safe paraphrases

These preserve attribution or uncertainty semantically but avoid the exact `alleged/claimed/argued` wording where possible.

| Source | Rewrite | Expected | Inferred | Result |
|---|---|---|---|---|
| S15_C009_02 `ALLEGED` | On body camera, Anthony took responsibility for the killing; the footage records his words, not their truth. | safe | ESTABLISHED | false positive |
| S15_C070_02 `ALLEGED` | The defense version put Metcalf first in the physical encounter. | safe | ESTABLISHED | false positive |
| S15_C101_02 `ALLEGED` | Anthony's account was self-defense against Metcalf. | safe | ESTABLISHED | false positive |
| S15_C164_02 `ALLEGED` | The defense accused prosecutors of breaking the character-evidence deal. | safe | ESTABLISHED | false positive |
| S15_C167_02 `DISPUTED` | The courtroom record contains competing accounts of whether the agreement covered testimony. | safe | DISPUTED | correctly accepted |

Results: 1/5 true negatives, 4/5 false positives, false-positive rate 80.0% on this challenge subset.

## Combined Hard-Set Rate

```text
10 cases total
true positives: 5
true negatives: 1
false positives: 4
false negatives: 0
false-positive rate on safe cases: 80.0%
false-negative rate on violation cases: 0.0%
overall accuracy: 60.0%
```

The checker is not ready for Phase 6 approval. Its detection of strengthening is useful, but its natural-language safe-paraphrase handling is not yet reliable.
