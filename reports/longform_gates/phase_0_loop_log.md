# Phase 0: Recon loop log

Iteration 1. Date: 2026-10-10. Scope: read-only repository and database reconnaissance.

## Target selection

Case 6, `The Karmelo Anthony Murder Case`, is the only case with a usable EditorialBlueprint in the current database:

- Blueprint id: `1`
- Story version id: `15`
- Blueprint status: `valid`
- Story version: `master`, English, 43,497 characters
- Beats: `50`
- Questions: `5`
- Facts: `21`
- Sources: `20`
- Contradictions: `3`
- Visual assets: `32` photos
- Original media segments: `0`
- Production scripts: `0`

This is the real-data target. The blueprint is persisted in `editorial_blueprints.blueprint_json`, and the master narration is persisted in `story_versions.story_text`. There is no persisted `production_scripts` row for case 6, so any later EpistemicContracts extraction must state explicitly that it is extracting from the finished master story text unless a production-script artifact is created in a later phase.

## Current blueprint shape

The exact top-level JSON keys are:

```text
central_question,editorial_thesis,human_thread,arcs,questions,beats
```

The exact beat keys are:

```text
id,act_id,paragraphs,summary,human_focus,purpose,emotional_load,information_density,mystery_intensity,attention,visual_intent,audio_intent,pause_after,music_intent,words,reveals,relies_on,opens,answers,unresolved,viewer_knows,open_questions
```

The exact question keys are:

```text
id,question,kind,opened_in,resolved_in,status
```

All 50 beats have `reveals`, `relies_on`, and `viewer_knows` arrays. Across the blueprint there are 41 reveal references, 97 relies-on references, and 1,396 viewer-knows entries. No beat has a `do_not_reveal` field and no beat has a `reveal_map` field. The current references are primitive evidence ids such as `F009`, `T007`, and `C003`; they are not persisted RevealGraph node ids. The validator only checks those ids against the in-memory evidence pack and emits warnings for unrevealed dependencies; it does not persist or query a reusable reveal graph.

## Exact commands and outputs

### `git status --short --branch`

```text
## task/host-manifest-and-pilot-run
```

### `sqlite3 truecrime.db ".tables"`

```text
agent_runs                   original_media_segments
audio_plans                  platform_variants
case_status_checks           production_scripts
cases                        research_jobs
chapter_plans                research_queries
claim_clusters               research_results
contradictions               research_source_links
cross_platform_funnels       short_form_concepts
discovery_candidates         short_form_metrics
documentary_jobs             short_form_publishing_plans
editorial_blueprints         short_form_scripts
facts                        sources
follow_up_candidates         story_versions
host_memories                transcript_claims
host_plans                   transcript_segments
host_scenes                  video_sources
host_segments                videos
media_usages                 visual_assets
monitor_runs                 visual_audits
music_tracks                 visual_plans
music_usages                 voice_performances
narrative_insights
```

### `sqlite3 truecrime.db ".schema editorial_blueprints"`

```text
CREATE TABLE editorial_blueprints (
    id INTEGER NOT NULL,
    case_id INTEGER NOT NULL,
    story_version_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL,
    evidence_fingerprint VARCHAR(32),
    story_text_hash VARCHAR(64),
    central_question TEXT,
    editorial_thesis TEXT,
    human_thread TEXT,
    blueprint_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    generation_provider VARCHAR(50),
    generation_model VARCHAR(200),
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(case_id) REFERENCES cases (id),
    FOREIGN KEY(story_version_id) REFERENCES story_versions (id)
);
CREATE INDEX ix_editorial_blueprints_status ON editorial_blueprints (status);
CREATE INDEX ix_editorial_blueprints_case_id ON editorial_blueprints (case_id);
CREATE INDEX ix_editorial_blueprints_story_version_id ON editorial_blueprints (story_version_id);
```

### `sqlite3 truecrime.db ... SELECT eb.id, eb.case_id, ...`

```text
id  case_id  story_version_id  version  status  json_bytes  kind    language  story_chars
1   6        15                1        valid   44900       master  en        43497
```

### `sqlite3 file:truecrime.db?mode=ro&immutable=1 ... SELECT ... FROM editorial_blueprints`

```text
id  case_id  beats  questions  validation_status
1   6        50     5          valid
```

### Field inventory

```text
beats  reveal_refs  rely_refs  known_refs  do_not_reveal_present  reveal_map_present
50     41           97         1396        0                      0
```

### `sqlite3 ... SELECT case_id, asset_type, COUNT(*) ... FROM visual_assets`

```text
case_id  asset_type  count
6        photo       32
```

### Evidence counts for case 6

```text
kind                     count
facts                    21
contradictions           3
sources                  20
original_media_segments  0
visual_assets            32
```

### `sqlite3 ... SELECT ... FROM production_scripts`

```text
(no output)
```

## Recon caveats recorded without hiding failures

The first aggregate query against the live database returned `Error: in prepare, database is locked (5)`. It was rerun with SQLite immutable read-only mode. The first case query attempted to select `cases.case_uid`; the actual database file predates that column even though the ORM model declares it, so that query returned `Error: in prepare, no such column: c.case_uid` and was corrected to use the columns physically present. An attempted query against `timeline_events` returned `Error: in prepare, no such table: timeline_events`; the current evidence tables are `facts`, `contradictions`, and `sources`.

These are schema/data facts, not suppressed test failures. No files other than this required report were changed in Phase 0.

## Gate status

Phase 0 recon is complete and intentionally paused for review. No RevealGraph or EpistemicContracts implementation has started. Recommended next step after approval: Phase 1 schema design and migrations, preserving case 6's existing primitive blueprint fields while adding persisted graph, exposure, asset-link, claim, and review records.
