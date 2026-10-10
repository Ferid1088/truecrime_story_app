# Phase 1: Data model and migration loop log

Iteration 1. Date: 2026-10-10. Result: stopped before implementation because an additional pre-existing schema/model discrepancy was found, as required by the build instructions.

## Known drift requested for this phase

The physical `cases` table is missing the ORM-declared `case_uid` column. This was confirmed during Phase 0 and is still the narrowly scoped migration item for Phase 1.

## Additional drift found before edits

The physical database contains `original_media_segments`, but the current ORM has no corresponding model or reference anywhere under `app/` or `tests/`. Exact database schema:

```text
CREATE TABLE original_media_segments (
    id INTEGER NOT NULL,
    case_id INTEGER NOT NULL,
    asset_id INTEGER NOT NULL,
    beat_id VARCHAR(20),
    source_start FLOAT NOT NULL,
    source_end FLOAT NOT NULL,
    language VARCHAR(10),
    transcript TEXT,
    story_use TEXT,
    translation_strategy_json TEXT NOT NULL,
    selected BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(case_id) REFERENCES cases (id),
    FOREIGN KEY(asset_id) REFERENCES visual_assets (id)
);
CREATE INDEX ix_original_media_segments_case_id ON original_media_segments (case_id);
CREATE INDEX ix_original_media_segments_asset_id ON original_media_segments (asset_id);
```

Exact ORM search:

```text
grep -RIn --exclude-dir=.git --exclude-dir=__pycache__ -E 'class OriginalMediaSegment|original_media_segments|OriginalMedia' app tests
(no output)
exit 0
```

This matters to the requested RevealGraph asset tagging because Phase 1 needs a correct persisted link to `OriginalMediaSegment` ids. I have not added a model, altered the existing table, or worked around it with an unvalidated integer field.

## Exact current status

```text
## task/host-manifest-and-pilot-run
?? reports/longform_gates/
```

No Phase 1 implementation files or database changes were made. The only worktree content is the required Phase 0 report and this Phase 1 stop report.

## Gate status

The additional discrepancy was authorized in the next turn. Phase 1 implementation is now present, but the full regression gate is paused with one pre-existing unrelated failure documented below. Phase 2 must not start until this gate is accepted or the regression is resolved.

## Implemented artifacts

- ORM models: `app/db/models.py`
  - `OriginalMediaSegment` maps the existing table without changing its columns.
  - `RevealGraph`, `RevealNode`, `RevealEdge`, `RevealExposure`, and `RevealAssetLink` persist the graph, dependencies, per-beat exposure, and visual/original-media links.
  - `EpistemicContractSet` and `EpistemicClaim` persist versioned, reviewable claim modality records.
- Migration path: `app/db/schema.py`
  - Adds only the missing nullable `cases.case_uid` column and its index when absent.
  - Existing startup `create_all` creates the new long-form tables; it does not alter the existing `original_media_segments` table.
- CRUD and deterministic validation: `app/longform/service.py`
  - Acyclic graph validation, orphan-reference checks, reachability, reveal-order dependency checks, and case-owned asset validation.
  - Graph, contract-set, and claim CRUD.
- HTTP API: `app/longform/api.py`, included by `app/main.py`.
- Tests: `tests/test_longform_phase1.py`.

## OriginalMediaSegment evidence

The model path is `app/db/models.py`, class `OriginalMediaSegment`.

Confirmed foreign keys, from both ORM metadata and the real table:

```text
cases.id <- original_media_segments.case_id
visual_assets.id <- original_media_segments.asset_id
```

Real database command:

```text
sqlite3 'file:truecrime.db?mode=ro&immutable=1' "PRAGMA foreign_key_list(original_media_segments);"
```

Exact output:

```text
0|0|visual_assets|asset_id|id|NO ACTION|NO ACTION|NONE
1|0|cases|case_id|id|NO ACTION|NO ACTION|NONE
```

Real case 6 count:

```text
sqlite3 'file:truecrime.db?mode=ro&immutable=1' -header -column "SELECT COUNT(*) AS case6_original_media_segments FROM original_media_segments WHERE case_id=6;"
```

```text
case6_original_media_segments
-----------------------------
0
```

The real table has no rows for any case currently. The test therefore records the real case-6 count as zero and separately round-trips a relational row in an isolated test database to verify both ORM relationships.

## Case UID migration evidence

The authorized narrow migration was applied to the real database:

```text
sqlite3 truecrime.db "ALTER TABLE cases ADD COLUMN case_uid VARCHAR(20); CREATE INDEX IF NOT EXISTS ix_cases_case_uid ON cases(case_uid);"
(no output)
exit 0
```

Existing IDs were backfilled through the application helper:

```text
DATABASE_URL=sqlite:////Users/feri/truecrime_story_app/truecrime.db PYTHONPYCACHEPREFIX=/tmp/truecrime-pycache .venv/bin/python -c 'from app.db.base import SessionLocal; from app.identity.titles import backfill_case_uids; db=SessionLocal(); print("backfilled", backfill_case_uids(db)); db.close()'
backfilled 6
exit 0
```

Verification:

```text
total_cases  missing_case_uids
-----------  -----------------
6            0
```

The seven new long-form tables were created on the real database with a targeted `Base.metadata.create_all(..., tables=[...])` call. The existing `original_media_segments` schema was not altered.

## Test evidence

Two consecutive focused Phase 1 runs both returned:

```text
6 passed, 1 warning in 0.09s
exit 0
```

The final focused run after adding explicit claim creation returned:

```text
6 passed, 1 warning in 0.12s
exit 0
```

Compile and whitespace checks:

```text
PYTHONPYCACHEPREFIX=/tmp/truecrime-pycache .venv/bin/python -m compileall -q app
(no output)
exit 0

git diff --check
(no output)
exit 0
```

The full existing long-form regression command returned:

```text
200 passed, 1 failed, 2 warnings in 55.07s
exit 1
```

The sole failure is `tests/test_host.py::test_host_director_plans_remembers_and_writes`, where the returned host segment status is `needs_review` instead of `valid` after earlier tests populate the shared test database with recent host segments. This failure reproduces in the existing long-form suite and passes when run alone; it is unrelated to the Phase 1 tables. The six earlier visual failures caused by a pre-existing `bytes`/`Path` mismatch were fixed narrowly in `app/documentary/visuals/images.py` by allowing `data_url` to accept both `Path` and `bytes`.

## Host regression audit requested before Phase 1 approval

Isolated command (run three times):

```text
PYTHONPYCACHEPREFIX=/tmp/truecrime-pycache .venv/bin/python -m pytest tests/test_host.py::test_host_director_plans_remembers_and_writes -q
```

```text
Run 1: . [100%] | 1 passed, 2 warnings in 0.09s | exit 0
Run 2: . [100%] | 1 passed, 2 warnings in 0.09s | exit 0
Run 3: . [100%] | 1 passed, 2 warnings in 0.08s | exit 0
```

Full regression command (run twice):

```text
PYTHONPYCACHEPREFIX=/tmp/truecrime-pycache .venv/bin/python -m pytest tests/test_documentary_foundations.py tests/test_chapters.py tests/test_story_order.py tests/test_visual_direction.py tests/test_visual_audit.py tests/test_visual_production.py tests/test_host.py tests/test_voice_blocks.py tests/test_spoken_audio.py tests/test_traceability.py -q
```

```text
Run 1: [35%] ...; [71%] ...F...; [100%]
FAILED tests/test_host.py::test_host_director_plans_remembers_and_writes
200 passed, 1 failed, 2 warnings in 54.82s | exit 1

Run 2: 200 passed, 1 failed, 2 warnings in 55.41s | exit 1
```

Both failures were the same assertion at `tests/test_host.py:255`:

```text
assert row.status == "valid" and row.language == "en"
E AssertionError: assert ('needs_review' == 'valid'
...
```

Dependency inspection:

```text
git diff -- app/agents/host.py app/documentary/host.py tests/test_host.py app/db/models.py app/db/schema.py app/main.py
```

The output contained changes only in `app/db/models.py`, `app/db/schema.py`, and `app/main.py`; no diff existed for the host files or the host test. `git log -8 --oneline --decorate` began with `aff4b9a WIP: short-form engine implementation`, `b397df5 Fix: documentary batch/resume endpoints crash with no running event loop`, and `85466cd Host manifest: register the 17 out...`; Phase 1 is uncommitted. The pytest fixture uses one shared temporary database and does not reset it per test. `app/documentary/host.py` queries host segments from other cases. Phase 1 did not change host query code or host model fields. The separate `app/documentary/visuals/images.py` change only widens `data_url` to accept `bytes`.

Pre-Phase 1 stash baseline:

```text
git stash push --include-untracked -m 'phase1 verification pre-change baseline'
Saved working directory and index state On task/host-manifest-and-pilot-run: phase1 verification pre-change baseline
stash@{0}: On task/host-manifest-and-pilot-run: phase1 verification pre-change baseline
```

With the stash applied, the isolated command returned:

```text
. [100%]
1 passed, 2 warnings in 0.07s
exit 0
```

Restoration:

```text
git stash pop
Dropped refs/stash@{0} (05fb63058c12dcf96e3ebdf8137008d335d949d9)
## task/host-manifest-and-pilot-run
 M app/db/models.py
 M app/db/schema.py
 M app/documentary/visuals/images.py
 M app/main.py
?? app/longform/
?? reports/longform_gates/
?? tests/test_longform_phase1.py
```

The Phase 1 worktree was restored without conflicts.

## Root-cause audit and isolation fix

The stashed full-suite probe produced this raw result:

```text
8 failed, 193 passed, 2 warnings in 42.54s
```

The six visual failures were the pre-existing `bytes`/`Path` failures in `data_url`. The host failure was also present on the stashed tree, but the reason the earlier post-change suite reached it differently was concrete: `app/documentary/visuals/images.py` now permits the visual-production pipeline to continue past thumbnail auditing. That pipeline writes `HostSegments` rows before `tests/test_host.py` runs. The shared fixture does not reset the database, and `recent_segments()` intentionally queries `HostSegments` from other cases, so those newly persisted rows enter the host anti-repetition check and can make the scripted dialogue fail.

The new RevealGraph/Epistemic tables are empty and are not queried by `recent_segments()`. Their creation does not shift host table ids or alter host migrations. The causal shared state is persisted `host_segments` history from an earlier test, exposed by the existing shared fixture and the host production query.

Fix applied:

```text
tests/conftest.py: added isolated_host_history fixture
tests/test_host.py: test_host_director_plans_remembers_and_writes now requests isolated_host_history
```

The fixture deletes only `HostSegments` and `HostMemory` for this integration test, commits the clean boundary, and the test creates its own earlier episode and memory. No test ordering was changed and no Phase 1 table was special-cased.

Focused verification:

```text
1 passed, 2 warnings in 0.12s
exit 0
```

Full regression run 1:

```text
201 passed, 2 warnings in 56.09s
exit 0
```

Full regression run 2:

```text
201 passed, 2 warnings in 54.99s
exit 0
```

## Phase 2 case 6 RevealGraph gate

Implementation and review artifacts:

```text
app/longform/service.py
scripts/build_reveal_graph.py
tests/test_longform_phase2.py
reports/longform_gates/phase_2_case6_reveal_graph/README.md
reports/longform_gates/phase_2_case6_reveal_graph/nodes.csv
reports/longform_gates/phase_2_case6_reveal_graph/edges.csv
reports/longform_gates/phase_2_case6_reveal_graph/exposures.csv
```

The source was the real persisted case 6 EditorialBlueprint id 1, status `valid`: 50 beats, 41 `reveals`, 97 `relies_on`, and 1,396 `viewer_knows` entries. The builder resolved all 41 IDs against the real case evidence pack: 21 facts, 17 timeline IDs derived from dated facts, and 3 contradictions.

The graph was persisted with the real database and returned:

```text
existing_before None
created_graph_id 1 status validated
nodes 41 edges 69 exposures 41
validation {'status': 'valid', 'nodes': 41, 'edges': 69, 'exposures': 41}
```

The corrected edge rule is recorded in the review README: only `relies_on` creates edges. `viewer_knows` verifies that each dependency is known and was first revealed earlier; it never creates an edge. The graph has 8 explicit dependency roots and all 41 nodes are reachable from those roots without inventing prerequisites.

Focused Phase 2 plus Phase 1 tests, run twice:

```text
7 passed, 1 warning in 0.16s
exit 0

7 passed, 1 warning in 0.14s
exit 0
```

Full existing long-form regression, run twice after the Phase 2 changes:

```text
201 passed, 2 warnings in 56.06s
exit 0

201 passed, 2 warnings in 54.95s
exit 0
```

## Phase 1 decision

Phase 1 implementation and focused acceptance tests are complete. The Phase 1 regression issue was isolated and fixed; the full suite is now green twice. Phase 2 is complete up to the required human review gate. No downstream spoiler-horizon or visual-tagging work has started.

## Phase 2 human-review audit: late chains and beats 45-50

The initial 768-edge graph was rejected as semantically over-connected. It was rebuilt with the corrected 69-edge rule. The late-story nodes inspected were:

```text
F011: On June 9, 2026, a Collin County jury found Anthony guilty of murder...
F012: The jury rejected a lesser manslaughter charge ... and Anthony was sentenced to 35 years...
F015: On August 22, 2026, Judge Michael Chitty denied Anthony's request for a new trial.
T014: The defense's motion for a new trial alleged that public access to the trial was materially restricted...
```

The corrected persisted graph parents are:

```text
F011 actual parents: 3: F013, F016, F018
F012 actual parents: 3: F013, F016, F018
F015 actual parents: 3: F013, F016, F018
T014 actual parents: 5: F016, F017, F018, F019, F020
```

For F011/F012/F015, the blueprint's explicit `relies_on` names were only:

```text
F013: Anthony did not testify at trial.
F016: The jury selected for Anthony's trial had no Black jurors; prosecutors struck three remaining Black potential jurors...
F018: On June 4, 2026, the auxiliary viewing room ... was eliminated...
```

These counts now match the blueprint's explicit narrative dependencies and are no longer inflated by all previously known material.

The raw persisted blueprint for beats 45-50 shows:

```text
B45 reveals=[]; relies_on=[F012,T010]; purpose=transition
B46 reveals=[]; relies_on=[F012,T010]; purpose=reveal
B47 reveals=[]; relies_on=[F017,T014]; purpose=investigation
B48 reveals=[]; relies_on=[F015,T012]; purpose=reveal; answers=[Q5]
B49 reveals=[]; relies_on=[F005,F006,F007,F021]; purpose=evidence
B50 reveals=[]; relies_on=[F001,F002,F012]; purpose=chapter_end; answers=[Q1]
```

All six beats have no `do_not_reveal` or `reveal_map` entries, and all six have zero persisted exposure rows. No late-story reference is missing: all 41 reveal nodes were first revealed by B40, and B45-B50 only revisit already-revealed material. B50 is consistent with a wrap-up beat: its summary draws a conclusion and answers Q1 without introducing a new evidence ID. B46 and B48 are marked with purpose `reveal` but contain no `reveals`, which is a blueprint/editorial inconsistency worth review, not a missing graph node.

Phase 2 remains stopped at the human review gate. No downstream phase has started.

## Phase 2 corrected dependency rebuild

The graph was rebuilt after the human-review finding. `relies_on` is now the only edge source. `viewer_knows` only verifies that each `relies_on` node is present and was first revealed earlier. Dependency-free nodes are explicit documentary-start roots; no artificial edges were added to connect them.

Rebuild output:

```text
deleted_old_graph 1
payload 41 69 41
validation {'status': 'valid', 'nodes': 41, 'edges': 69, 'exposures': 41}
created_graph_id 1 status validated
F011 parents ['F013', 'F016', 'F018']
F012 parents ['F013', 'F016', 'F018']
F015 parents ['F013', 'F016', 'F018']
T014 parents ['F016', 'F017', 'F018', 'F019', 'F020']
```

Focused Phase 1 + Phase 2 tests after rebuild:

```text
7 passed, 1 warning in 0.21s
7 passed, 1 warning in 0.13s
```

Full existing long-form regression after rebuild:

```text
201 passed, 2 warnings in 55.54s
201 passed, 2 warnings in 54.48s
```

The B46/B48 `purpose=reveal` with empty `reveals` condition remains logged as an editorial/blueprint data-quality flag only; it was not changed in this phase.

## Phase 3 spoiler-horizon gate

Implementation:

```text
app/longform/service.py: spoiler_horizon, check_spoiler_references
app/longform/api.py: GET /api/reveal-graphs/{graph_id}/spoiler-horizon
app/longform/api.py: POST /api/reveal-graphs/{graph_id}/spoiler-check
tests/test_longform_phase3.py
```

Real case 6 query outputs from persisted `reveal_graphs.id=1`:

```text
B10 allowed: ['C003', 'F001', 'F002', 'F007', 'F008', 'F009', 'F010',
              'T001', 'T002', 'T005', 'T006', 'T007', 'T008']
B10 forbidden: ['C001', 'C002', 'F003', 'F004', 'F005', 'F006', 'F011',
                'F012', 'F013', 'F014', 'F015', 'F016', 'F017', 'F018',
                'F019', 'F020', 'F021', 'T003', 'T004', 'T009', 'T010',
                'T011', 'T012', 'T013', 'T014', 'T015', 'T016', 'T017']
B10 requested [F011,F012,F015]: forbidden [F011,F012,F015], allowed []

B15 allowed: ['C001', 'C003', 'F001', 'F002', 'F003', 'F004', 'F005', 'F006',
              'F007', 'F008', 'F009', 'F010', 'F021', 'T001', 'T002', 'T003',
              'T004', 'T005', 'T006', 'T007', 'T008']
B15 forbidden: ['C002', 'F011', 'F012', 'F013', 'F014', 'F015', 'F016', 'F017',
                'F018', 'F019', 'F020', 'T009', 'T010', 'T011', 'T012', 'T013',
                'T014', 'T015', 'T016', 'T017']
B15 requested [F011,F012,F015]: forbidden [F011,F012,F015], allowed []

B45 allowed: all 41 graph nodes
B45 forbidden: []
B45 requested [F011,F012,F015]: forbidden [], allowed [F011,F012,F015]
```

Focused Phase 1 + Phase 2 + Phase 3 tests, run twice:

```text
8 passed, 1 warning in 0.17s
8 passed, 1 warning in 0.15s
```

Full existing long-form regression, run twice:

```text
201 passed, 2 warnings in 54.92s
201 passed, 2 warnings in 54.51s
```

Phase 3 stops at the review gate. Phase 4 has not started.

## Phase 4 visual/audio reveal-tagging gate

Implementation:

```text
app/longform/service.py: add_reveal_asset_link, check_spoiler_visual_assets
app/longform/api.py: asset-links and spoiler-visual-check endpoints
tests/test_longform_phase4.py
```

Real case 6 inventory verification:

```text
visual_assets: 32
editorial_review_required: 29
unknown: 2
public_domain: 1 (VIS_000031, Wikimedia stadium image)
verified: 0
original_media_segments: 0
```

The requested earlier inventory description said 30 editorial-review-required assets; the current table actually contains 29 plus one public-domain asset. This discrepancy was reported and asserted from the real rows rather than silently normalized.

Persisted links on `reveal_graphs.id=1`:

```text
VIS_000001 -> F005  (gray utility-knife image)
VIS_000003 -> F015  (judge denied new trial)
VIS_000013 -> F011  (murder verdict)
VIS_000013 -> F012  (35-year sentence)
VIS_000017 -> F001  (fatal track-meet stabbing)
VIS_000031 -> F001  (stadium location)
visual_link_count 6
case6_original_media_segments 0
```

Visual-only spoiler checks against real graph 1:

```text
VIS_000001 at B10: linked node F005; forbidden_visual_asset_ids [1]
VIS_000001 at B15: linked node F005; allowed_visual_asset_ids [1]
VIS_000013 at B15: linked nodes F011,F012; forbidden_visual_asset_ids [13]
```

Focused Phase 1-4 tests, run twice:

```text
9 passed, 1 warning in 0.16s
9 passed, 1 warning in 0.14s
```

Full existing long-form regression, run twice:

```text
201 passed, 2 warnings in 55.07s
201 passed, 2 warnings in 54.68s
```

Audio tagging is explicitly empty for case 6 because the real `original_media_segments` table has zero rows. Phase 4 stops at the review gate; Phase 5 has not started.

## Phase 5 EpistemicContracts extraction gate

Implementation:

```text
app/longform/epistemic.py
scripts/extract_epistemic_contracts.py
tests/test_longform_phase5.py
reports/longform_gates/phase_5_case6_epistemic/README.md
```

Source and coverage:

```text
source: StoryVersion.id=15, case 6 persisted master story
total sentence spans: 304
explicit question spans excluded: 6
declarative denominator: 298
claims extracted: 298
coverage: 100.0% of declarative sentence spans
evidence-linked claims: 278/298 (93.3%)
contract_set_id: 1
status: in_review
claim review statuses: all proposed
```

The extractor stores exact character spans, beat IDs from the real blueprint paragraph ranges, proposed modality, script source reference, and matched real evidence IDs. The sample report contains 12 real claims for human review. Modalities are deterministic proposals, not approvals; the sample intentionally includes assignments such as the quoted confession proposed as `ALLEGED`, which requires editorial correction or confirmation.

Focused Phase 1-5 tests, run twice:

```text
10 passed, 1 warning in 0.25s
10 passed, 1 warning in 0.16s
```

Full existing long-form regression, run twice:

```text
201 passed, 2 warnings in 55.06s
201 passed, 2 warnings in 54.75s
```

Phase 5 stops at the human modality-review gate. Phase 6 has not started.

## Phase 5 atomic-proposition revision gate

Command:

```text
PYTHONPATH=. python scripts/extract_epistemic_contracts.py 6 15 1 --replace
```

Output:

```text
created contract_set_id=1 claims=311 coverage=100.0%
```

Persisted coverage:

```text
298 declarative source sentences -> 311 atomic claims
declarative_spans_covered: 298/298 = 100.0%
split_source_sentences: 12/298 = 4.03%
evidence_linked_claims: 291/311 = 93.57%
```

Focused Phase 5 test, two runs:

```text
1 passed, 1 warning in 0.02s
1 passed, 1 warning in 0.02s
```

Long-form regression (`tests/test_longform_phase1.py` through `tests/test_longform_phase5.py`), two runs:

```text
10 passed, 1 warning in 0.17s
10 passed, 1 warning in 0.14s
```

Full repository regression, two runs, both with the same unrelated existing failure:

```text
1 failed, 823 passed, 2 warnings in 163.75s
1 failed, 823 passed, 2 warnings in 139.25s
FAILED tests/test_thumbnails.py::test_api_flow
E KeyError: 'status' at tests/test_thumbnails.py:331
```

The Phase 5 changes do not touch `tests/test_thumbnails.py` or thumbnail implementation files. The full-repository result is reported as observed and is not marked green.

## Phase 5 scope and baseline clarification

The historical `201 passed` entries do not include the command that produced them. The current explicit long-form command is:

```text
PYTHONPATH=. pytest -q tests/test_longform_phase1.py tests/test_longform_phase2.py tests/test_longform_phase3.py tests/test_longform_phase4.py tests/test_longform_phase5.py
```

It currently collects 10 tests. The current full repository command is:

```text
PYTHONPATH=. pytest -q
```

It currently collects 824 tests, producing 823 passes and one thumbnail failure. Therefore `201` is neither the current long-form count nor the current full-repository count; its historical command scope was not recorded and must not be used as the regression label going forward.

Stash baseline check:

```text
git stash push --include-untracked -m phase5-preexisting-thumbnail-check
PYTHONPATH=. pytest -q tests/test_thumbnails.py::test_api_flow
```

The stashed tree produced the same failure:

```text
1 failed, 1 warning in 26.22s
FAILED tests/test_thumbnails.py::test_api_flow
E KeyError: 'status' at tests/test_thumbnails.py:331
```

The stash was restored successfully with `git stash pop`, exit 0.

The initial 51 mixed-modality candidates were a high-recall lexical review scan. The implemented splitter applied a narrower structural rule and split only 12 sentences that matched implemented separable patterns. The remaining 39 were not all false positives: patterns such as `S15_C016`, `S15_C067`, `S15_C070`, `S15_C074`, `S15_C101`, and `S15_C164-S15_C166` contain attributed propositions that the current implementation did not split. This is an identified coverage gap, not a claim that the 12 splits exhaust the mixed-modality scan. Phase 5 remains at review and is not approved.

## Phase 5 high-recall atomic-proposition revision

Command:

```text
PYTHONPATH=. python scripts/extract_epistemic_contracts.py 6 15 1 --replace
```

Output:

```text
created contract_set_id=1 claims=330 coverage=100.0%
```

Persisted coverage:

```text
298 declarative source sentences -> 330 atomic claims
declarative_spans_covered: 298/298 = 100.0%
split_source_sentences: 30/298 = 10.07%
evidence_linked_claims: 310/330 = 93.94%
```

The high-recall pass explicitly covers `S15_C016`, `S15_C067`, `S15_C070`, `S15_C074`, `S15_C101`, `S15_C164`, `S15_C165`, and `S15_C166`, plus additional attribution cases including `S15_C154` and `S15_C167`. `S15_C165` is split into a reporting act plus two distinct attributed propositions.

Focused persisted Phase 5 test, two runs:

```text
1 passed, 1 warning in 0.03s
1 passed, 1 warning in 0.02s
```

The thumbnail failure remains explicitly out of scope and is documented in the Phase 5 review report: `tests/test_thumbnails.py::test_api_flow`, `KeyError: 'status'`, reproduced on the full-worktree stashed tree.

## Phase 6 compression-safety gate

Implementation:

```text
app/longform/compression.py
scripts/evaluate_compression_safety.py
tests/test_longform_phase6.py
reports/longform_gates/phase_6_case6_compression/README.md
```

Real case 6 evaluation:

```text
660 labeled rewrites from 330 persisted claims
expected violations: 61
expected safe rewrites: 599
true positives: 61
true negatives: 599
false positives: 0 (0.0%)
false negatives: 0 (0.0%)
```

Required adversarial check:

```text
S15_C009_02: ALLEGED -> rewrite "I did it." -> inferred ESTABLISHED -> correctly rejected
```

Required safe paraphrase:

```text
S15_C080: ESTABLISHED -> "The record establishes that the strike caused a rapidly fatal two-inch chest wound." -> ESTABLISHED -> accepted
```

Long-form regression through Phase 6, two runs:

```text
11 passed, 1 warning in 0.27s
11 passed, 1 warning in 0.24s
```

Phase 6 stops at the review gate. Phase 7 has not started.

## Phase 6 hard challenge audit

The 660-case baseline was template-generated, not human-authored. The separate natural-language challenge produced:

```text
5 strengthened violations: 5 true positives, 0 false negatives
5 safe compressed paraphrases: 1 true negative, 4 false positives
false-negative rate: 0.0%
false-positive rate: 80.0%
overall accuracy: 60.0%
```

The false positives came from semantically attributed rewrites such as `Anthony's account was self-defense against Metcalf` and `The defense version put Metcalf first in the physical encounter`; the current lexical checker inferred `ESTABLISHED`. The checker was not changed during this audit. Full case details are in `reports/longform_gates/phase_6_case6_compression/hard_challenge.md`. Phase 6 remains unapproved.

## Phase 6 attribution-aware hard challenge revision

Diagnosis: the checker recognized explicit hedge words but missed semantically attributed forms such as possessive `account`, `version`, `accusation`, and `as recounted by the defense`. The inference now treats those as attributed propositions while retaining explicit established framing as a strengthening signal.

Round 1, the original 5 violations plus 5 safe paraphrases:

```text
TP 5, TN 5, FP 0, FN 0
false-positive rate 0.0%, false-negative rate 0.0%, accuracy 100.0%
```

Round 2, a new 5-violation plus 5-safe set with different wording:

```text
TP 5, TN 5, FP 0, FN 0
false-positive rate 0.0%, false-negative rate 0.0%, accuracy 100.0%
```

The 660-case baseline remains documented as template-generated and is not treated as a natural-language benchmark. Full details are in `reports/longform_gates/phase_6_case6_compression/hard_challenge_v2.md`. Phase 6 remains at the review gate.

## Phase 6 third attribution challenge

The third 10-case set used uncoded attribution forms and prose-level strengthening:

```text
TP 3, TN 0, FP 7, FN 0
false-positive rate 100.0%, false-negative rate 0.0%, accuracy 30.0%
```

All three disguised strengthening violations were caught. All seven safe paraphrases using forms such as `per Anthony's telling`, `as Anthony put it afterward`, `the narrative Anthony gave`, indirect dialogue, and distancing framing were falsely rejected. The result is documented in `reports/longform_gates/phase_6_case6_compression/hard_challenge_v3.md`.

Across the three manual rounds there are 30 hand-authored challenge cases, but Phase 6 is not approved and Phase 7 has not started because this third round exposes a material attribution false-positive gap.

## Phase 6 semantic classifier implementation gate

Implementation:

```text
app/longform/compression.py: SemanticAttributionClassifier and three-way gate
config/models.json: consistency_checker temperature 0.0, seed 17
app/core/ai_config.py: optional generation seed
app/providers/generation/openai_compat.py: seed request field
scripts/evaluate_semantic_compression.py: 30 accumulated cases plus 10 fresh cases
reports/longform_gates/phase_6_case6_compression/semantic_classifier_audit.md
```

The production dependency is the configured `APIMasterGenerationProvider` through the existing `consistency_checker` role. The live 40-case structured run did not return within the provider request window and was interrupted; no live result was counted. The explicit fallback run returned:

```text
total 40; accept 0; reject 0; human_review 40
classifier_source deterministic_fallback
```

The fallback emitted 40 warnings. Phase 6 remains unapproved and Phase 7 has not started.

Follow-up live evaluation after diagnosing the provider latency:

```text
PYTHONPATH=. python scripts/evaluate_semantic_compression.py --concurrency 1 --timeout 75 \
  > /tmp/semantic_live_results.json 2> /tmp/semantic_live_progress.log
```

The run completed all 40 cases. Progress counts were `START=40`, `DONE=40`, and
`FALLBACK=0`. The live provider was `llm:apimaster/gpt-5.6-luna` for every case.

```text
total: 40
accept: 22
reject: 18
human_review: 0
expected_safe: 22
expected_violations: 18
safe -> ACCEPT: 22
safe -> REJECT: 0
violation -> REJECT: 18
violation -> ACCEPT: 0
```

This is 0% false positives, 0% false negatives, and 0% abstention on the 40-case
manual set. The evaluator now logs `START` only after acquiring the concurrency
semaphore, so queued cases are not reported as active calls. Phase 6 remains at
the review gate; Phase 7 has not started.
