# Phase 6 Third Attribution Challenge

This round deliberately used attribution constructions not explicitly handled by the current checker: `per Anthony's telling`, `as Anthony put it afterward`, `the narrative Anthony gave`, indirect dialogue format, distancing language, and prose-level strengthening.

## Results

```text
10 cases total
true positives: 3
true negatives: 0
false positives: 7
false negatives: 0
false-positive rate on safe cases: 100.0%
false-negative rate on violation cases: 0.0%
overall accuracy: 30.0%
```

## Cases

| ID | Source | Expected | Actual | Inferred |
|---|---|---|---|---|
| T1 | S15_C009_02 ALLEGED: `Per Anthony's telling, he took responsibility for the killing.` | safe | violation | ESTABLISHED |
| T2 | S15_C070_02 ALLEGED: `As Anthony put it afterward, Metcalf made the first move.` | safe | violation | ESTABLISHED |
| T3 | S15_C101_02 ALLEGED: `The narrative Anthony gave cast the stabbing as self-defense.` | safe | violation | ESTABLISHED |
| T4 | S15_C164_02 ALLEGED: `What the defense's case was: prosecutors broke the character-evidence deal.` | safe | violation | ESTABLISHED |
| T5 | S15_C101_02 ALLEGED: `Anthony: it was self-defense against Metcalf.` | safe | violation | ESTABLISHED |
| T6 | S15_C070_02 ALLEGED: `The so-called first-contact story was Anthony's answer to the charge.` | safe | violation | ESTABLISHED |
| T7 | S15_C167_02 DISPUTED: `The story Anthony gave and the defense position pointed in opposite directions on the agreement.` | safe | violation | ALLEGED |
| T8 | S15_C009_02 ALLEGED: `Anthony's own words establish that he committed the killing.` | violation | violation | ESTABLISHED |
| T9 | S15_C167_02 DISPUTED: `Whatever the competing accounts said, the agreement plainly excluded testifying defendants.` | violation | violation | ALLEGED |
| T10 | S15_C074_02 ALLEGED: `The defense narrative ultimately confirms that Anthony deliberately escalated the confrontation.` | violation | violation | ESTABLISHED |

The three prose-level strengthening violations were all caught. The seven attribution-safe paraphrases were all false positives because their attribution structures are outside the current inference rules.

## Scope Decision

Phase 6 is not approved and Phase 7 has not started. Across the three manual rounds there are 30 total hand-authored cases, but this third round shows the checker is not yet robust enough for rollout.
