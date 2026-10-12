# Live database schema diagnosis

## Current PostgreSQL production target (2026-10-12)

The application now defaults to the app-owned local PostgreSQL database
`postgresql+psycopg://truecrime@127.0.0.1:5432/truecrime` in
`app/core/config.py:6-10`. A local `TRUECRIME_DATABASE_URL` override is still
accepted by `app/core/config.py:8` and is loaded by `scripts/dev_up.sh:48-62`.
The empty `truecrime` database was upgraded through Alembic `0010` with
`TRUECRIME_DATABASE_URL=... .venv/bin/python -m alembic upgrade head`; Alembic
reported all ten revisions and PostgreSQL committed transactional DDL. The
subsequent `alembic check` reported `No new upgrade operations detected`, so
the live PostgreSQL schema has no ORM/migration autogenerate differences.

The PostgreSQL catalog confirms both required relationships:

| Constraint | Definition |
|---|---|
| `fk_agent_runs_documentary_job_id` | `agent_runs.documentary_job_id REFERENCES documentary_jobs(id)` |
| `story_versions_master_version_id_fkey` | `story_versions.master_version_id REFERENCES story_versions(id)` |

The query used `pg_constraint` and restricted `agent_runs` and
`story_versions` to foreign-key constraints. PostgreSQL is provisioned only
by `scripts/pg_up.sh`; it creates the app-owned `truecrime` and
`truecrime_test` databases and never selects or drops other databases.

The remainder of this file is the retained Phase A SQLite drift diagnosis.
It is historical evidence for the test-only `truecrime.db`, not the current
production schema. The Phase B SQLite allowlist below applies only to SQLite
tests and reconciliation copies; it does not apply to PostgreSQL.

Date of inspection: 2026-10-12. This document contains schema metadata only; it
does not contain row values or secrets. The source was opened with SQLite
`mode=ro`. The verified backup was created with SQLite's backup API.

## Databases inspected

The historical inspection resolved `sqlite:///./truecrime.db` by default from
the working directory (`app/core/config.py:7-10`, `app/db/base.py:5-12`) before
the PostgreSQL default was introduced.
`TRUECRIME_DATABASE_URL` wins over `DATABASE_URL`; neither was exported during
this inspection. The live database is therefore:

| Path | Size | SHA-256 |
|---|---:|---|
| `truecrime.db` | 20,877,312 bytes | `780984e03ddc42a430bf7237a7e9837a5d6e364c905233f015f24296ad24ca14` |

`data/truecrime.db` exists but is zero bytes and is not the resolved database.
Other SQLite files found are archival or transfer copies under `backups/`,
`Claude outputs/`, and `tc_transfer/`; none is selected by the default URL.
The local PostgreSQL server is running with databases `appliance`, `postgres`,
`template0`, and `template1`. The current application does not select any of
them without an environment override.

## Verified backup

Backup: `data/backups/truecrime-20261012-030545+0200.db`

| File | SHA-256 |
|---|---|
| live | `780984e03ddc42a430bf7237a7e9837a5d6e364c905233f015f24296ad24ca14` |
| backup | `47d52be736ed5c2d3ef213c3aa675a1dae3a0cd036211f5841fb9fa7524b015b` |

The backup passed `PRAGMA integrity_check` (`ok`) and had zero foreign-key
violations. The directory is ignored by `.gitignore` (`data/backups/`).

## Version state

The live database has no `alembic_version` table. It is therefore treated by
`app/db/migrate.py:107-151` as a pre-Alembic database and is refused because it
does not exactly match either known starting signature.

## Schema comparison summary

Comparisons use SQLite table metadata, columns including type/nullability/default,
indexes, and foreign keys. Foreign-key ordering identifiers are ignored.

| Comparison | Missing tables | Extra tables | Tables with differences | Extra columns | Missing columns | Column metadata differences | Index differences | FK differences |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| live vs frozen baseline | 0 | 0 | 1 | 3 | 0 | 0 | 0 | 0 |
| live vs 0001–0009 | 8 | 0 | 11 | 0 | 3 | 27 | 11 | 2 |
| live vs ORM physical SQLite schema | 8 | 0 | 11 | 0 | 3 | 27 | 11 | 2 |

### Live vs frozen Alembic baseline

All 55 baseline tables are present. `agent_runs` has three extra columns:

- `documentary_job_id INTEGER NULL`
- `language VARCHAR(20) NULL`
- `pipeline_stage VARCHAR(80) NULL`

No baseline columns, indexes, or foreign keys are missing or altered.

### Live vs migrations 0001–0009

Missing tables: `app_settings`, `artifact_deps`, `artifacts`, `backup_runs`,
`edit_log`, `override_log`, `policy_events`, and `provider_calls`.

The following are the complete per-table differences:

| Table | Column differences | Index differences | Constraint differences |
|---|---|---|---|
| `agent_runs` | `fallback_used`: live nullable, expected NOT NULL; live has the three extra columns listed above | Missing `ix_agent_runs_documentary_job_id`, `ix_agent_runs_language`, `ix_agent_runs_pipeline_stage` | Missing FK `documentary_job_id → documentary_jobs.id` |
| `cases` | `aliases_json`, `identifiers_json`, `people_json`, `resolution_status`: live nullable, expected NOT NULL | Missing `ix_cases_resolution_status` | None |
| `discovery_candidates` | `aliases_json`, `people_json`, `resolution_status`, `source_urls_json`, `state`: live nullable, expected NOT NULL | Missing `ix_discovery_candidates_case_id`, `ix_discovery_candidates_state` | None |
| `documentary_jobs` | Missing `worker_id`, `heartbeat_at`, `auto_resumes`; `production_type` live nullable, expected NOT NULL | None | None |
| `epistemic_claims` | `assertion_role`: live nullable, expected NOT NULL; `origin`: live `VARCHAR(20)`, expected `VARCHAR(40)` | Missing `ix_epistemic_claims_assertion_role`, `ix_epistemic_claims_parent_claim_key` | None |
| `facts` | `chunk_ids_json`, `locations_json`, `original_language`, `people_json`, `transcript_claim_ids_json`: live nullable, expected NOT NULL | None | None |
| `sources` | `content_status`, `status`: live nullable, expected NOT NULL | None | None |
| `story_versions` | `is_best`, `kind`, `language`, `similarity_status`, `status`: live nullable, expected NOT NULL | Missing `ix_story_versions_case_id`, `ix_story_versions_status` | Missing FK `master_version_id → story_versions.id` |
| `videos` | `render_profile`: live nullable, expected NOT NULL | None | None |
| `visual_assets` | None | Missing `ix_visual_assets_entity_key` | None |
| `visual_audits` | `detail_json`: live nullable, expected NOT NULL | None | None |

For columns whose type and default are unchanged, the only metadata difference
is nullability. The migration schema also has server defaults for several
columns; those are recorded in the detailed comparison but are not row data.

Exact changed-column metadata (`live` → `0001–0009`) is:

| Table.column | Live type / nullable / default | Expected type / nullable / default |
|---|---|---|
| `agent_runs.fallback_used` | BOOLEAN / NULL / `0` | BOOLEAN / NOT NULL / `0` |
| `cases.aliases_json`, `identifiers_json`, `people_json` | TEXT / NULL / `'[]'` | TEXT / NOT NULL / `'[]'` |
| `cases.resolution_status` | VARCHAR(30) / NULL / `'UNKNOWN'` | VARCHAR(30) / NOT NULL / `'UNKNOWN'` |
| `discovery_candidates.aliases_json`, `people_json`, `source_urls_json` | TEXT / NULL / `'[]'` | TEXT / NOT NULL / `'[]'` |
| `discovery_candidates.resolution_status` | VARCHAR(30) / NULL / `'UNKNOWN'` | VARCHAR(30) / NOT NULL / `'UNKNOWN'` |
| `discovery_candidates.state` | VARCHAR(20) / NULL / `'suggested'` | VARCHAR(20) / NOT NULL / `'suggested'` |
| `documentary_jobs.production_type` | VARCHAR(20) / NULL / `'original'` | VARCHAR(20) / NOT NULL / `'original'` |
| `epistemic_claims.assertion_role` | VARCHAR(40) / NULL / `'NARRATOR_ASSERTION'` | VARCHAR(40) / NOT NULL / `'NARRATOR_ASSERTION'` |
| `epistemic_claims.origin` | VARCHAR(20) / NOT NULL / none | VARCHAR(40) / NOT NULL / none |
| `facts.chunk_ids_json`, `locations_json`, `people_json`, `transcript_claim_ids_json` | TEXT / NULL / `'[]'` | TEXT / NOT NULL / `'[]'` |
| `facts.original_language` | VARCHAR(20) / NULL / `'en'` | VARCHAR(20) / NOT NULL / `'en'` |
| `sources.content_status` | VARCHAR(30) / NULL / `'summary_only'` | VARCHAR(30) / NOT NULL / `'summary_only'` |
| `sources.status` | VARCHAR(50) / NULL / `'active'` | VARCHAR(50) / NOT NULL / `'active'` |
| `story_versions.is_best` | BOOLEAN / NULL / `0` | BOOLEAN / NOT NULL / `0` |
| `story_versions.kind` | VARCHAR(20) / NULL / `'direct'` | VARCHAR(20) / NOT NULL / `'direct'` |
| `story_versions.language` | VARCHAR(20) / NULL / `'fa'` | VARCHAR(20) / NOT NULL / `'fa'` |
| `story_versions.similarity_status` | VARCHAR(30) / NULL / `'not_evaluated'` | VARCHAR(30) / NOT NULL / `'not_evaluated'` |
| `story_versions.status` | VARCHAR(20) / NULL / `'draft'` | VARCHAR(20) / NOT NULL / `'draft'` |
| `videos.render_profile` | VARCHAR(10) / NULL / `'preview'` | VARCHAR(10) / NOT NULL / `'preview'` |
| `visual_audits.detail_json` | TEXT / NULL / `'{}'` | TEXT / NOT NULL / `'{}'` |

Grouped rows contain multiple columns with identical metadata. The 27-column
count expands those grouped entries individually.

### Live vs current ORM models

The table, column, index, and foreign-key difference set is the same as the
live-vs-migrations list above. The ORM comparison additionally represents
Python-side defaults as no database server default, while the migrations encode
some defaults in SQLite DDL. The actionable differences are therefore the same
eight missing tables, three missing columns, 27 nullability/type/default
differences, 11 missing indexes, two missing foreign keys, and the three
extra `agent_runs` columns.

## Migrations versus models on an empty database

An empty SQLite database was upgraded through `0010`. Its `alembic_version` was
`0010`. Alembic `compare_metadata(..., compare_type=True)` returned `[]` against
the current ORM metadata. There is no actionable migration-vs-model drift by
Alembic's comparison rules. The physical server-default differences noted
above are intentional differences between migration defaults and Python-side
ORM defaults, not autogenerate findings.

## Why the live file drifted

The exact command that produced this particular live file is not recorded.
Git history does establish the sequence of schema ownership changes:

- `54a852a` introduced Alembic and froze the pre-Alembic schema as the legacy
  baseline.
- `fa0e067` added worker bookkeeping to `documentary_jobs` and migration 0004.
- `732a417`, `018b303`, `cb1b525`, and `15136e9` added provider-call, backup,
  policy/override, and artifact/edit tables through migrations 0005–0009.
- Migration 0007 (`migrations/versions/0007_documentary_cost_attribution.py`)
  is the official source for the `agent_runs` attribution columns, but the live
  columns lack its indexes and foreign key.

The evidence is consistent with a pre-Alembic/legacy file that received a
partial manual or `create_all`-style update, while later migration revisions
were never applied or stamped. It does not prove which manual operation caused
the three `agent_runs` columns.

## Extra live columns

All three extra columns are used by current code and must remain part of the
official schema:

| Column | Usage |
|---|---|
| `agent_runs.documentary_job_id` | ORM field and FK at `app/db/models.py:250-252`; populated from the task-local context at `app/services/tracking.py:16-30` and `55-58` |
| `agent_runs.language` | ORM field at `app/db/models.py:253`; populated through the same tracking context at `app/services/tracking.py:16-30` and `55-58` |
| `agent_runs.pipeline_stage` | ORM field at `app/db/models.py:254`; populated through the same tracking context at `app/services/tracking.py:16-30` and `55-58` |

## Phase B reconciliation allowlist

Migration `0010_reconcile_legacy_schema` is additive and idempotent. The
verified backup copy was stamped at `0009` and upgraded to `0010`; the source
file was not opened for writing. Its 56 pre-existing table row counts were
unchanged across all 55 pre-existing user tables, `PRAGMA integrity_check` returned `ok`, and
`PRAGMA foreign_key_check` returned no rows.

The reconciled SQLite copy was compared with a fresh database at `0010`. The
only permitted differences are this explicit SQLite allowlist:

1. Existing nullable columns remain nullable. Tightening them would require a
   SQLite table rebuild and could require data backfills; `0010` does not make
   that destructive or semantic decision.
2. Existing `VARCHAR` lengths remain as-is. SQLite does not enforce those
   lengths, so widening or narrowing them is not performed by reconciliation.
3. The two foreign keys on legacy tables are not added on the SQLite copy:
   `agent_runs.documentary_job_id -> documentary_jobs.id` and
   `story_versions.master_version_id -> story_versions.id`. SQLite requires a
   table rebuild to add them; `0010` adds them only on Postgres.

No extra tables, columns, indexes, or constraints were present outside this
allowlist. The permanent migration test and
`scripts/check_model_migrations.py` verify the fresh migration schema against
the ORM metadata; the copy-specific comparison is performed by
`scripts/db_reconcile.sh`.

The copy had no documentary or research jobs in `running`, `queued`,
`interrupted`, or `partial` status. The safe worker check used
`TRUECRIME_JOB_MAX_AUTO_RESUMES=0` and observed no job-state or provider-call
ledger changes. API, settings, operations, artifact-list, and dashboard
backup smoke checks were run against the copy.

Risks remain SQLite table-rebuild behavior for future nullability/FK work,
incorrectly stamping a database at the wrong revision, and starting services
with a copy path that resolves relative artifact paths differently.
