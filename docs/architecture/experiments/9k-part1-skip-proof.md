# 9K Part 1 — real-pipeline skip proof

Evidence was produced by `tests/run_9k_skip_proof.py` from a persistent
four-language job built from the real research stage. The final complete run
was saved under `/var/folders/4t/4k_v5bnj1ng81bs7wj7g3sgw0000gn/T/tc-9k-skip-proof-scx55scu`.

## Verify mode

The table counts one initial policy decision per stage. `match` is emitted only
when that stage initially would skip and the forced producer rerun still
matched; initially `would-run` stages are not falsely counted as mismatches.

| perturbation | would-skip | would-run | match | mismatch |
|---|---:|---:|---:|---:|
| unchanged | 1 | 45 | 1 | 0 |
| naming | 1 | 45 | 1 | 0 |
| spoken version de | 1 | 13 | 1 | 0 |
| one voice take de | 1 | 45 | 1 | 0 |

## Enforce mode

Policy decisions, rather than a producer's intentional `{"skipped": true}`
detail, define `executed` and `reused`. The real fixture disables automatic
title generation, so the naming perturbation is proven as
`INCREMENTAL_RUN` (input and output hash changed), while the producer returns
its documented disabled result and no external naming provider is contacted.

| scenario | policy executed | policy reused | provider calls | wall clock |
|---|---:|---:|---|---:|
| unchanged | 42 | 4 | asr 12, generation 18, maps 1 | 41.179 s |
| naming | 43 | 3 | asr 12, generation 18, maps 1 | 41.028 s |
| spoken version de | 10 | 4 | none | 3.308 s |

The SOUND stages were reused as required in all three scenarios:
`research`, `master_story`, and `master_approval` were `skip` for unchanged;
the same three were `skip` after naming; and `research`, `master_story`, and
`master_approval` were `skip` after the German spoken-version edit. The full
forced reruns made the same provider calls, so provider calls saved were
`asr=0`, `generation=0`, `maps=0` in each scenario. This is expected for this
fixture: the SOUND stages have persisted outputs and do not call providers.

The saved-output snapshot was byte-equal to the mode-off forced rerun for
`unchanged`, `naming`, and `spoken_de` (`true` for all three; named volatile
fields only).

The API response for the unchanged rerun reported these stages as reused with
reason `all hash and policy gates passed`: `research`, `master_story`,
`master_approval`, `blueprint`, `audio_plan`, `chapters`, and `naming`.

## Gate proofs and harness correction

An out-of-band edit of the persisted master story on a fresh rerun produced:

```
research: skip — all hash and policy gates passed
master_story: run — input hash changed; output hash changed
master_approval: skip — all hash and policy gates passed
```

A pending `EditLog(status="running")` on the master-story artifact produced a
`master_story: run` decision with `pending user edit or lock`.

The first attempt exposed a harness defect: the provider fixture reseeded the
master output after every research stage, overwriting an out-of-band edit
before the hash gate. The fixture now seeds only when
`SOUNDNESS_SEED_MASTER=1` while building the initial template; reruns consume
the saved master. The rerun input fingerprint also excludes incremental policy
bookkeeping and normalizes disposable run paths, while retaining producer
configuration and semantic research/naming inputs.
