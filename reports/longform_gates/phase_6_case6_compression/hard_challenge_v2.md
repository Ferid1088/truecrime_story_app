# Phase 6 Attribution-Paraphrase Fix Audit

## Diagnosis

The original false positives were caused by a narrow lexical attribution detector. It recognized `alleged`, `claimed`, `argued`, and similar words, but treated semantically attributed phrases such as `Anthony's account`, `the defense version`, and `as recounted by the defense` as established narration. The fix recognizes attribution structures and explicit language separating recorded words from truth, while keeping direct declarative rewrites violations.

## Round 1: Original Hard Set

The same five strengthened violations and five safe paraphrases from the prior audit were rerun after the fix.

```text
true positives: 5
true negatives: 5
false positives: 0
false negatives: 0
false-positive rate: 0.0%
false-negative rate: 0.0%
overall accuracy: 100.0%
```

The five violations were `Anthony was the killer`, `Metcalf initiated the physical confrontation`, `The stabbing was a justified act of self-defense`, `Prosecutors breached the agreement`, and `The agreement categorically excluded defendants who testified`. All were rejected.

The five safe rewrites included `Anthony's account was self-defense against Metcalf`, `The defense version put Metcalf first`, and `The defense accused prosecutors...`. All were accepted after attribution-aware inference was added.

## Round 2: New Wording

This set used different wording from Round 1.

```text
true positives: 5
true negatives: 5
false positives: 0
false negatives: 0
false-positive rate: 0.0%
false-negative rate: 0.0%
overall accuracy: 100.0%
```

New violation examples:

- `Anthony's push of Metcalf is an established fact.`
- `Anthony deliberately escalated the confrontation.`
- `The prosecutors violated the agreement.`
- `Anthony stabbed Metcalf in the chest.`
- `The agreement plainly excluded testifying defendants.`

New safe examples:

- `The footage preserves Anthony's own version of events, without resolving its truth.`
- `Metcalf's side of the encounter, as recounted by the defense, began with contact.`
- `Anthony's self-defense account remains his version of what happened.`
- `The defense's accusation was that prosecutors broke the deal.`
- `The record preserves two incompatible accounts of whether the agreement covered testimony.`

## Interpretation

Both 10-case rounds now pass, but this remains a small hand-authored challenge set. The larger 660-case corpus remains template-generated. The result supports the attribution fix; it does not justify treating the 660-case 0%/0% figure as a natural-language benchmark.
