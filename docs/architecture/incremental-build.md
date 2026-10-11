# Incremental build architecture

Status: regenerated from the current code on 2026-10-12. The artifact graph
described below is a design target; it is not yet a database table.

## Addressable units and IDs today

The database is the current source of identity. Cases, story versions,
editorial blueprints, production scripts, visual assets, visual audits,
host scenes, videos, and documentary jobs have integer primary keys. A
documentary stage is currently addressed by its job stage name, with a
language suffix where applicable (for example `spoken:de` or `render:fa`).
The stage list is stored in `documentary_jobs.stages_json`; stage detail and
attempt history are stored alongside it in that JSON. Short-form concepts,
scripts, platform variants, publishing plans, and metrics also have integer
IDs, but are not yet linked to the long-form job graph.

Existing stable content identifiers include:

| Unit | Current identity/fingerprint |
| --- | --- |
| Case and story | `Case.id`, `StoryVersion.id`, evidence fingerprints and text hashes in story services |
| Documentary run | `DocumentaryJob.id`, stage name, language, `stages_json` |
| Spoken script | language-specific script hash recorded by `pipeline/language.py` |
| Voice and host media | text SHA-256, voice SHA-256, avatar-video SHA-256, persisted sidecars |
| Visual asset | `VisualAsset.id`, `asset_code`, file SHA-256 and perceptual hash |
| Visual audit | `VisualAudit.key`, asset code, sentences JSON and verdict |
| Render | `Video.id`, job/script relationship, output path and metadata |

Hashes are not uniform today: some are full SHA-256 values, some are short
prefixes used in filenames or cache keys, and some stages use a JSON/text
fingerprint. An implementation must preserve the full hash in the graph even
when a display filename uses a prefix.

## Current render assembly

The language pipeline produces a production script, narration/audio and
visual decisions. `app/documentary/render/engine.py` builds the ffmpeg filter
graph and writes to an atomic temporary output before renaming it. Intro
rendering is cached under `data/intros/<language>/`; the render result is
serialized in the documentary job and the resulting `Video` row. The render
stage must remain the last consumer of the approved stage outputs. A graph
implementation should record the exact input artifact IDs and hashes used by
each assembly, rather than infer dependencies from a filesystem scan.

## Dependency and invalidation table

| Artifact | Depends on | Invalidates when changed |
| --- | --- | --- |
| Research/evidence package | case, source contents, research configuration | master story and all downstream documentary artifacts |
| Master story | evidence package, story prompt/config, edit scope | blueprint, spoken versions, audio, visuals, chapters, host, render |
| Blueprint/acts | master story, director config | audio plan, spoken versions, chapters, visuals, host, render |
| Spoken language version | master story/blueprint, language, localization edit | that language's audio, visuals, chapters, host and render only |
| Audio plan | spoken version, audio config, approved cues | narration mix and that language's render |
| Visual plan/assets | blueprint, spoken sentences, rights, visual research | audited assets and render for the affected scope |
| Audited video piece/photo | source media, cut, meaning, target sentences | only the shot/beat and render that consumes it |
| Chapter/timeline cards | acts, already-told events, language text | that language's overlays and render |
| Host segment | host plan, host text, studio/outfit, language audio | that segment and render |
| Channel intro | channel/language/size concept and logo | render assembly for that channel/size |
| Rendered film | all approved inputs above, profile, ffmpeg/render config | publication approval for that language |
| Short-form bundle | finished long film, approved beats, language and platform policy | only the short item; never the long film |

The first implementation should invalidate by exact dependency edges, not by
deleting all artifacts for a case. An edit to one language must not invalidate
other languages; an item edit must void approval for that item only.

## Proposed artifact-graph schema

Create an `artifact_graph` table with these required fields:

| Field | Meaning |
| --- | --- |
| `artifact_id` | stable application ID for the produced artifact |
| `type` | master, audio, visual, chapter, host, intro, render, short, etc. |
| `scope_case_id` | case scope, nullable only for global assets |
| `scope_production_id` | documentary production/job scope |
| `scope_language` | language scope, nullable for language-independent artifacts |
| `scope_item_id` | beat, shot, segment or short item scope when applicable |
| `input_hash` | canonical hash of all normalized inputs and policy versions |
| `output_path` | repository/data-relative output path; never an absolute path in audit context |
| `status` | pending, building, approved, omitted, degraded, invalidated, failed |
| `depends_on` | JSON list of artifact IDs and edge metadata |
| `version` | graph/schema and producer version |
| `created_by` | stage/agent/service identifier |
| `edit_id` | the user edit that produced this version, nullable |

The graph must also store created/updated timestamps and an error/reason JSON
field in the eventual migration, while keeping the required field names
above. Context is sanitized before persistence: no secrets, PII, or absolute
paths.

## Invalidation algorithm

1. Normalize the edit or upstream input into a canonical intent and compute
   its scope: case, production, language, stage and optional item IDs.
2. Recompute the input hash for the directly affected artifact. If it is
   unchanged, keep the artifact and report a no-op.
3. Mark that artifact `invalidated` and breadth-first traverse `depends_on`.
   Mark descendants invalidated only when their dependency edge intersects the
   edit scope.
4. Keep old rows and files addressable for review/undo. Create a new `edit_id`
   and new artifact version for regenerated outputs.
5. Re-run only invalidated stages, using existing approved artifacts for all
   other edges. Auditors must approve the replacement before a render can
   consume it.
6. Recompute the render assembly hash from the selected artifact versions.
   Publication approval is bound to that hash; any changed item loses only its
   own approval.

`DocumentaryJob.stages_json` remains the operational resume ledger during the
transition. It records pending/running/done/skipped/degraded/failed stage
entries and attempt history. `resume_documentary_job()` resets only failed,
running and blocked stages; saved stage details remain. The artifact graph
will become the durable dependency/selection ledger, while `stages_json`
continues to provide a compact worker checkpoint until all stages write graph
edges.

## Edit-by-prompt flow

The user-facing edit request should be persisted as an intent before it is
executed. A minimal intent schema is:

```json
{
  "edit_id": "uuid",
  "actor": "operator",
  "prompt": "short free-text instruction",
  "target": {"case_id": 1, "production_id": 2, "language": "de", "item_ids": [17]},
  "requested_fields": ["chapter_text"],
  "reason": "editorial reason"
}
```

The flow is: parse intent; validate allowed fields and language/item scope;
preview the dependency impact and expected cost; ask the user to execute;
produce a new candidate; show before/after and exact changed IDs/hashes; run
the relevant auditor; then offer accept, redo with another instruction, undo,
or escalation. Accept writes the new graph version and override event. Redo
creates a new edit. Undo selects the prior approved version without deleting
history. Auditor escalation leaves the item out or stops the stage according
to the configured exhaustion policy.

## Implementation steps, tests and risk

1. Add normalized hash helpers and a graph migration/API (medium risk).
   Test deterministic hashes, sanitized context, Postgres/SQLite migration,
   and dependency edge persistence.
2. Make research, master story and blueprint writers emit graph nodes
   (medium/high risk). Test selective invalidation and unchanged-input reuse.
3. Add per-language audio, visual, chapter and host edges (high risk).
   Test that an `en` edit does not rebuild `de`, and an item edit does not
   void unrelated approvals.
4. Make render consume a graph-selected manifest and bind `Video` approval to
   the assembly hash (high risk). Test atomic output, missing dependency
   rejection and resume after interruption.
5. Add the prompt-edit preview/execution UI and audit escalation (medium
   risk). Test accept/redo/undo and audit rejection paths end to end.
6. Link the short-form bundle only after the long film reaches its finished
   state (medium risk). Test that every short failure is isolated from long
   film completion and release.
