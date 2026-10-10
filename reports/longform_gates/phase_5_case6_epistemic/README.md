# Phase 5: Case 6 EpistemicContracts Review Sample

- Contract set: `epistemic_contract_sets.id=1`
- Status: `in_review`; all claims remain `proposed`
- Source: persisted master `StoryVersion.id=15`
- Source sentences: **298 declarative sentences** out of 304 total spans
- Atomic claims: **330**
- Declarative spans covered: **298/298 = 100.0%**
- Split source sentences: **30/298 = 10.07%**
- Evidence-linked atomic claims: **310/330 = 93.94%**
- Questions excluded: 6

Every atomic claim retains the full source sentence span and `source_sentence_text`. Split claims share a `parent_claim_key`; `claim_text` is the normalized atomic proposition. All records remain deterministic proposals for human review.

## Previously Missed Attribution Cases

These are the eight previously identified missed IDs plus two additional high-recall cases. Each now has a separate established reporting act and attributed proposition, with further clause splitting where the proposition contains distinct claims.

### S15_C016_01 / S15_C016_02
- Beat: `B03`; parent: `S15_C016`; speaker: `Anthony`
- Reporting act: `ESTABLISHED` — As the officer handcuffs him, Anthony emotionally tells him with desperate urgency.
- Attributed proposition: `ALLEGED` — the other person put hands on him.
- Full source sentence: As the officer handcuffs him, Anthony emotionally tells him with desperate urgency that the other person put hands on him.

### S15_C067_01 / S15_C067_02
- Beat: `B11`; parent: `S15_C067`; speaker: `witnesses`
- Reporting act: `ESTABLISHED` — The script attributes an account to witness testimony presented in the case record.
- Attributed proposition: `ALLEGED` — Metcalf pushed Anthony.
- Full source sentence: According to witness testimony presented in the case record, Metcalf pushed Anthony.

### S15_C070_01 / S15_C070_02
- Beat: `B12`; parent: `S15_C070`; speaker: `defense`
- Reporting act: `ESTABLISHED` — The defense maintained.
- Attributed proposition: `ALLEGED` — Metcalf made physical contact first, shoving or grabbing Anthony after the Centennial student refused to leave the shelter.

### S15_C074_01 / S15_C074_02
- Beat: `B12`; parent: `S15_C074`; speaker: `prosecution`
- Reporting act: `ESTABLISHED` — State prosecutors argued.
- Attributed proposition: `ALLEGED` — Anthony had deliberately provoked and escalated the situation after being told to leave a team area where he did not belong.

### S15_C101_01 / S15_C101_02
- Beat: `B17`; parent: `S15_C101`; speaker: `Anthony`
- Reporting act: `ESTABLISHED` — From the very moment of his arrest on the stadium perimeter, Anthony repeatedly maintained.
- Attributed proposition: `ALLEGED` — he had acted in self-defense against Metcalf’s physical push.

### S15_C154_01 / S15_C154_02
- Beat: `B24`; parent: `S15_C154`; speaker: `prosecution`
- Reporting act: `ESTABLISHED` — Prosecutors argued.
- Attributed proposition: `ALLEGED` — Anthony had introduced lethal force into an ordinary dispute.
- Full source sentence: Prosecutors argued that Anthony had introduced lethal force into an ordinary dispute.

### S15_C164_01 / S15_C164_02
- Beat: `B26`; parent: `S15_C164`; speaker: `defense`
- Reporting act: `ESTABLISHED` — Anthony’s defense team alleged.
- Attributed proposition: `ALLEGED` — prosecutors had violated an off-the-record agreement regarding character evidence.

### S15_C165_01 / S15_C165_02 / S15_C165_03
- Beat: `B26`; parent: `S15_C165`; speaker: `defense`
- Reporting act: `ESTABLISHED` — The script attributes an account to the defense.
- Attributed proposition: `ALLEGED` — the parties had agreed to restrict specific background information from entering the trial.
- Attributed proposition: `ALLEGED` — prosecutors signaled they would introduce damaging character material if Anthony took the stand.
- Full source sentence: According to the defense, the parties had agreed to restrict specific background information from entering the trial, but prosecutors signaled they would introduce damaging character material if Anthony took the stand.

### S15_C166_01 / S15_C166_02
- Beat: `B26`; parent: `S15_C166`; speaker: `defense`
- Reporting act: `ESTABLISHED` — That maneuver, defense attorneys claimed.
- Attributed proposition: `ALLEGED` — effectively blocked him from testifying in his own defense.
- Full source sentence: That maneuver, defense attorneys claimed, effectively blocked him from testifying in his own defense.

### S15_C167_01 / S15_C167_02 / S15_C167_03
- Beat: `B26`; parent: `S15_C167`; speaker: `prosecution`
- Reporting act: `ESTABLISHED` — Prosecutor Bill Wirskye firmly denied the accusation.
- Disputed proposition: `DISPUTED` — the unrecorded agreement never applied to a defendant who chose to take the witness stand.
- Disputed proposition: `DISPUTED` — The prosecution maintained that the defense had opened the door to such evidence.
- Full source sentence: Prosecutor Bill Wirskye firmly denied the accusation, arguing that the unrecorded agreement never applied to a defendant who chose to take the witness stand, and maintaining that the defense had opened the door to such evidence.

## Additional Review Samples

### S15_C005_01 / S15_C005_02
- Beat: `B01`; parent: `S15_C005`; speaker: `Officer Eduardo Cortez`
- Reporting act: `ESTABLISHED` — Officer Cortez activated his radio and reported the detention to dispatch.
- Attributed proposition: `ALLEGED` — The detained person was described as an alleged suspect.

### S15_C009_01 / S15_C009_02
- Beat: `B02`; parent: `S15_C009`; speaker: `Anthony`
- Reporting act: `ESTABLISHED` — Anthony spoke the recorded statement.
- Quoted proposition: `ALLEGED` — “I’m not alleged, sir, I did it.”
- The recording establishes that Anthony said it; it does not independently establish the truth of “I did it.”

### S15_C078_01 / S15_C078_02
- Beat: `B13`; parent: `S15_C078`; speaker: `witnesses`
- Reporting act: `ESTABLISHED` — Witnesses testified.
- Attributed proposition: `ALLEGED` — after Metcalf pushed him, Anthony pulled out a knife and stabbed Metcalf directly in the chest.

## Known Unrelated Failure

`tests/test_thumbnails.py::test_api_flow` fails with `KeyError: 'status'` at `tests/test_thumbnails.py:331`. A full-worktree stash check reproduced the same failure on the pre-Phase-5 tree. It is logged as a pre-existing bug outside this specification and was not changed here.
