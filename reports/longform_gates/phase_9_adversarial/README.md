# Phase 9: Adversarial Reveal and Epistemic Verification

## Command

```text
PYTHONPATH=. python scripts/phase9_adversarial_verify.py
```

This command reads the real persisted case-6 graph 1 and approved contract set
1. It does not create fixtures or alter the database.

## Exact Results

Graph:

```text
status: validated
nodes: 41
edges: 69
exposures: 41
```

Early spoiler adversary, `B10`, requesting `F011`, `F012`, and `F015`:

```text
allowed_references: []
forbidden_references: ['F011', 'F012', 'F015']
```

The complete `B10` allowed set was:

```text
C003 F001 F002 F007 F008 F009 F010 T001 T002 T005 T006 T007 T008
```

Late spoiler check, `B45`, requesting the same three nodes:

```text
allowed_references: ['F011', 'F012', 'F015']
forbidden_references: []
```

Visual adversary, real asset `VIS_000013` / database id `13`, linked to
`F011` and `F012`, queried at `B15`:

```text
forbidden_visual_asset_ids: [13]
allowed_visual_asset_ids: []
asset_nodes: {'13': ['F011', 'F012']}
```

Unknown reveal reference:

```text
rejected: True
error: Unknown reveal references: ['NOT_A_NODE']
```

Epistemic adversaries:

```text
contract_status: approved
claims: 330
approved_claims: 330

S15_C009_02
source_modality: ALLEGED
rewrite: Anthony did it
passed: False
rewritten_modality: ESTABLISHED

S15_C080
source_modality: ESTABLISHED
safe rewrite: The record establishes that the strike caused a rapidly fatal two-inch chest wound.
passed: True
rewritten_modality: ESTABLISHED
```

## Verification

```text
PYTHONPATH=. pytest -q tests/test_longform_phase9.py
1 passed, 1 warning in 0.04s
```

Implementation:

```text
scripts/phase9_adversarial_verify.py
tests/test_longform_phase9.py
```

Phase 9 passes for the tested real case-6 adversarial set. The original-audio
known gap from Phase 8 remains documented and does not affect these reveal and
epistemic checks.
