# Incremental build and artifact graph

This is an evidence-backed description of the current documentary pipeline and the
shadow-only artifact graph. Current behaviour is marked with source references. Design
text marked **PROPOSED** is not implemented.

## 1. Purpose and non-goals

The current job stores operational progress in `DocumentaryJob.stages_json`; the stage
runner resumes completed entries and does not skip work from artifact hashes
(`app/documentary/pipeline/base.py:94`, `app/documentary/pipeline/base.py:101`). The
shadow graph records addressable outputs and dependencies, but is write-only
(`app/services/artifact_shadow.py:122`, `app/core/ai_config.py:214`).

Non-goals of this task are hash-aware skipping, partial rebuilds, edit-by-prompt
execution, segment rendering, user media, media reuse, audio recovery, timeline-policy
changes, shorts, publishing, and god-file splitting. Those remain **PROPOSED**.

## 2. Artifact inventory

The graph has one declarative entry for each row below (`app/core/artifact_dependencies.py:16`,
`app/core/artifact_dependencies.py:42`). “Per-language” means the registry row carries
the language; “shared” means it carries no language.

| type | scope | storage today | stable ID | inputs actually read | producer | consumers | hash/cache today | reuse today |
|---|---|---|---|---|---|---|---|---|
| master_story | shared | `story_versions` | row ID | case/facts/prompts | `app/documentary/jobs.py:241` | approval, blueprint | `StoryVersion.text_hash` | latest row |
| beats | shared | `editorial_blueprints.blueprint_json` | member IDs on save | master text | `app/agents/blueprint.py:52` | audio, visuals, cards | blueprint story hash | latest valid row |
| audio_blocks | shared | `audio_plans.plan_json` | block/cue IDs on save | blueprint | `app/agents/audio_plan.py:49` | voice, music | no skip hash | latest row |
| spoken_version | per-language | `story_versions` | row ID/language | master story/language | `app/documentary/jobs.py:308` | voice/captions | row text hash | latest branch row |
| voice_takes | per-language | performance/voice rows/files | voice/block IDs | spoken text/voice settings | `app/documentary/pipeline/language.py:66` | timing/script | file metadata | stage resume |
| audio_timing | per-language | voice manifest JSON | manifest/block IDs | take durations/timestamps | `app/documentary/pipeline/language.py:105` | visuals/cards/music/captions | no skip hash | stage resume |
| visual_needs | shared | visual requirements JSON | requirement IDs | beats | `app/agents/visual_planner.py:35` | research/plan | no skip hash | latest plan |
| visual_asset | shared | `visual_assets` rows/files | asset row/code | source media/verification | `app/documentary/jobs.py:480` | shots/pieces | file/verification data | audited rows |
| visual_shots | shared | visual plan JSON | shot IDs | needs/assets | `app/agents/visual_director.py:96` | pieces/plan | no skip hash | latest plan |
| visual_pieces | shared | asset/piece rows/files | piece IDs | assets/shot cuts | `app/documentary/visuals/pieces.py:265` | plan/audit | source metadata | audited rows |
| visual_plan | shared | `visual_plans.plan_json` | plan member IDs | needs/shots/pieces/story order | `app/documentary/jobs.py:497` | production | no skip hash | unless refresh |
| chapter_plan | shared | chapter plan row/JSON | row/card IDs | master/beats | `app/agents/chapters.py:207` | cards | no skip hash | latest valid |
| chapter_cards | per-language | cards JSON | card IDs | plan/localized timing | `app/agents/chapters.py:207` | production | no skip hash | regenerated |
| timeline_cards | per-language | cards JSON | card IDs | told events/language | `app/agents/chapters.py:207` | production | no skip hash | regenerated |
| intro | per-language | `data/intros/<lang>/` | cached fingerprint/path | identity/language/size | `app/documentary/intros.py:119` | renderer | intro fingerprint | cached file |
| music_cues | per-language | manifest placements | cue IDs | direction/timing/library | `app/documentary/music.py:597` | production/mixer | usage history | stage resume |
| captions | per-language | production JSON/SRT | caption IDs | text/word timing | `app/documentary/production/script.py:1249` | renderer | no skip hash | rebuilt |
| production_script | per-language | `production_scripts.script_json` | member IDs | spoken/voice/timing/visuals/cards/music/config | `app/documentary/production/script.py:1348` | critique/render | row/version | latest row |
| final_render | per-language | relative path/`render_json` | row ID/path | script/audio/intro/render config | `app/documentary/render/engine.py:525` | UI/download | no skip hash | stage resume |

The source registry records scope and output registration (`app/services/artifact_shadow.py:29`,
`app/services/artifact_shadow.py:122`). Rows are observability, not a cache authority.

## 3. Pipeline stage sequence and resume

`plan_stages()` is the authority for the exact `stages_json[].name` keys
(`app/documentary/jobs.py:208`):

| exact key | inputs | output/detail today |
|---|---|---|
| `research` | case/research providers | research job/fact counts |
| `master_story` | research/facts | master story-version ID |
| `master_approval` | master/auditor notes | approved version/history |
| `blueprint` | master | blueprint row ID/status |
| `audio_plan` | blueprint | audio-plan row ID/status |
| `spoken:<lang>` | master/language | spoken version ID/status |
| `host_plan` | blueprint/host config, optional | host-plan detail |
| `chapters` | blueprint/master/chapter config | chapter-plan detail |
| `naming` | case/title data, optional | naming detail |
| `film_length` | spoken/audio estimates | duration decision |
| `visual_needs` | blueprint | requirements detail |
| `visual_research` | needs/search providers | candidate counts |
| `visual_check` | candidates/render profile | verification counts |
| `visual_plan` | needs/candidates/audio plan | visual-plan/shots |
| `visual_gaps` | weak sentences/plan/audio | gap-search detail |
| `host:<lang>` | host plan/language | host-segment detail |
| `performance:<lang>` | spoken version | performance row |
| `voice:<lang>` | performance/audio plan | narration/mix manifest |
| `production:<lang>` | manifest/visual/cards/language | production-script ID |
| `critique:<lang>` | production script | critique detail |
| `visual_audit:<lang>` | script shots/audits | audit/replacement detail |
| `render:<lang>` | script/audio/intro | render metadata/path |

The shared keys are created first and language branches follow the job definition
(`app/documentary/jobs.py:217`, `app/documentary/jobs.py:534`). The runner writes status,
attempts, timestamps, detail, and errors (`app/documentary/pipeline/base.py:105`,
`app/documentary/pipeline/base.py:123`). On resume, `done`, `skipped`, and `degraded`
entries are skipped; pending or failed entries run again (`app/documentary/pipeline/base.py:101`).

## 4. Final render today and per-segment analysis

The renderer reads a production script, resolves narration, writes SRT, creates raw RGB
video input, and maps script audio and subtitles (`app/documentary/render/engine.py:525`,
`app/documentary/render/engine.py:563`, `app/documentary/render/engine.py:574`). It
encodes H.264/AAC with mov_text and atomically renames the `.part` output
(`app/documentary/render/engine.py:575`, `app/documentary/render/engine.py:616`).

The frame graph grades stills, reads moving clips, draws cards/subtitles, applies
`cv2.addWeighted` crossfades or eased black fades, and eases the opening and final fades
(`app/documentary/render/engine.py:591`, `app/documentary/render/engine.py:597`,
`app/documentary/render/engine.py:607`). Clip sound is never an input; documentary
audio is the only encode audio (`app/documentary/render/engine.py:1`).

When an intro exists, FFmpeg receives a fourth input and `_intro_audio_graph` delays and
mixes it with ducked film audio; otherwise script audio is mapped directly
(`app/documentary/render/engine.py:477`, `app/documentary/render/engine.py:568`). Intro
files are language/size/fingerprint cached (`app/documentary/intros.py:24`,
`app/documentary/intros.py:119`). There is no separate `loudnorm` filter or audio
crossfade in the final renderer; loudness comes from prepared audio.

Per-segment rendering is **PROPOSED**. It must preserve absolute PTS, narration sample
continuity, one loudness pass, intro ducking windows, visual transition overlap,
chapter/timeline offsets, and subtitle offsets. Risks are encoder delay, reset clocks,
duration drift, double normalization, and crossfades consuming spoken words. Proposed
stitch: one codec/time base per segment, validate exact join PTS, concatenate, apply
loudness once, then audit LUFS tolerance, duration drift, and sample discontinuity.

## 5. Dependency table

This is the exact declarative table in `app/core/artifact_dependencies.py:42`.

| artifact | scope | depends on | invalidates |
|---|---|---|---|
| master_story | shared | — | beats, spoken_version, chapter_plan |
| beats | shared | master_story | audio_blocks, visual_needs, chapter_plan, timeline_cards, downstreams |
| audio_blocks | shared | beats | voice_takes, audio_timing, music_cues, captions, production_script, final_render |
| spoken_version | per-language | master_story | voice_takes, audio_timing, music_cues, captions, production_script, final_render (same language) |
| voice_takes | per-language | audio_blocks, spoken_version | audio_timing, music_cues, captions, production_script, final_render (same language) |
| audio_timing | per-language | voice_takes | music_cues, captions, production_script, final_render (same language) |
| visual_needs | shared | beats | visual_shots, visual_plan, production_script, final_render |
| visual_asset | shared | — | visual_shots, visual_pieces, visual_plan, production_script, final_render |
| visual_shots | shared | visual_needs, visual_asset | visual_pieces, visual_plan, production_script, final_render |
| visual_pieces | shared | visual_asset, visual_shots | visual_plan, production_script, final_render |
| visual_plan | shared | visual_needs, visual_shots, visual_pieces | production_script, final_render |
| chapter_plan | shared | master_story, beats | chapter_cards, production_script, final_render |
| chapter_cards | per-language | chapter_plan, timeline_cards | production_script, final_render (same language) |
| timeline_cards | per-language | beats | chapter_cards, production_script, final_render (same language) |
| intro | per-language | — | final_render (same language) |
| music_cues | per-language | audio_blocks, audio_timing | production_script, final_render (same language) |
| captions | per-language | spoken_version, audio_timing | production_script, final_render (same language) |
| production_script | per-language | spoken_version, voice_takes, audio_timing, visual_plan, chapter_cards, timeline_cards, captions, music_cues | final_render (same language) |
| final_render | per-language | production_script, intro | — |

Timing is a separate per-language artifact produced after voice rendering
(`app/documentary/pipeline/language.py:105`), while audio direction is shared and made
from the blueprint (`app/documentary/jobs.py:290`). Therefore one Persian duration change
does not stale German rows, while a shared beat change affects every language. `spoken_version`
depends on `master_story`, not `beats`, because its producer receives master and language
(`app/documentary/jobs.py:313`, `app/documentary/jobs.py:316`).

The `visual_asset` node limits replacement invalidation to its shot/piece dependents.
Shadow source payloads include persisted rows and documentary/voice/audio/music/render
configuration (`app/services/artifact_shadow.py:69`, `app/services/artifact_shadow.py:115`);
these must become explicit public fingerprint inputs before hash-aware skipping.

## 6. Artifact-graph schema

Migration 0009 and the ORM define `artifacts`: `id VARCHAR(180) PRIMARY KEY`, `case_id
INTEGER NULL`, `production_id INTEGER NULL`, `language VARCHAR(20) NULL`, `type VARCHAR(80)`,
`scope_json TEXT`, `input_hash VARCHAR(64)`, `output_hash VARCHAR(64) NULL`, `output_ref
TEXT NULL`, `status VARCHAR(20)`, `version INTEGER`, `created_by VARCHAR(120) NULL`,
`edit_id VARCHAR(100) NULL`, `stage_job_id INTEGER NULL`, `created_at`, and `updated_at`
(`app/db/models.py:1007`, `migrations/versions/0009_artifact_tables.py:15`).
`artifact_deps` has integer `id`, two string artifact foreign keys, and unique
`(artifact_id, depends_on_id)` (`app/db/models.py:1036`, `migrations/versions/0009_artifact_tables.py:47`).
Artifact scope/status, both edge directions, and edit scope/status are indexed
(`migrations/versions/0009_artifact_tables.py:36`, `migrations/versions/0009_artifact_tables.py:65`).

`edit_log` has `id`, `created_at`, `actor`, `scope_type`, `scope_id`, `instruction_text`,
`intent_json`, `scope_preview_json`, `status`, `result_json`, and `parent_edit_id`
(`app/db/models.py:1049`). It does not duplicate `override_log` or `policy_events`
(`app/db/models.py:1667`, `app/db/models.py:1690`). The **PROPOSED** state machine is
`fresh → stale → building → fresh`, with `building → failed|omitted`; 0009 creates tables
only and does not implement transitions (`migrations/versions/0009_artifact_tables.py:94`).
`stages_json` remains operational resume state; graph writes are best-effort
(`app/services/artifact_shadow.py:171`).

## 7. ID scheme

New members use UUID4-backed type prefixes, for example
`beat_550e8400-e29b-41d4-a716-446655440000`, `shot_...`, `chapter_card_...`,
`timeline_card_...`, and `caption_...` (`app/core/member_ids.py:12`,
`app/core/member_ids.py:68`). IDs are assigned once, persisted in JSON, never positional,
and never derived from text. Existing semantic IDs such as `B01` remain unchanged
(`app/core/member_ids.py:46`).

The real producers call the helper for blueprints, audio plans, visual plans, chapters,
and production scripts (`app/agents/blueprint.py:52`, `app/agents/audio_plan.py:49`,
`app/agents/visual_planner.py:35`, `app/agents/visual_director.py:96`,
`app/agents/chapters.py:207`, `app/documentary/production/script.py:1354`). A legacy
member without an ID gets deterministic UUID5 from parent/type/original position and
`legacy_id: true` in memory; it is not rewritten until the producer saves
(`app/core/member_ids.py:96`).

## 8. Invalidation algorithm

The current registry marks rows stale and traverses reverse edges with cycle detection
(`app/services/artifacts.py:86`, `app/services/artifacts.py:113`). The **PROPOSED**
hash-aware algorithm is:

```text
DFS the reachable graph with colors; reject a back edge before committing
queue requested IDs; affected = requested IDs
while queue: pop an ID; add unseen reverse-edge dependents
mark each affected row stale once with the reason
return the affected set grouped by type
```

Repeated invalidation is idempotent because stale rows are not duplicated and the same
reverse-edge traversal returns the same set (`app/services/artifacts.py:113`).

## 9. Edit-by-prompt flow

**PROPOSED** intent JSON schema:

```json
{"instruction":"string","scope":{"language":"en|null","beat_ids":["beat_..."]},"requested_changes":[{"field":"string","value":{}}],"reason":"string"}
```

The policy layer validates fields/scope, previews dependencies, and escalates story,
fact, or spoiler edits to the relevant auditors. Scope expansion is explicit. Preview
returns `{rebuilds:N,total:M,affected_by_type:{},estimated_cost_usd:null,estimated_time_seconds:null}`.
Execution records before/after hashes and offers accept, redo, or undo; a running job
must reject a stale preview or queue execution. These flows are not implemented; the
current API is read-only (`app/documentary/api.py:45`, `app/documentary/api.py:62`).

## 10. API endpoints

Current endpoints are `GET /api/documentary/{case_id}/artifacts` with type/language/status
filters and `POST /api/documentary/{case_id}/artifacts/scope-preview` with artifact IDs
(`app/documentary/api.py:45`, `app/documentary/api.py:62`). **PROPOSED** edit endpoints
are preview, execute, redo, and undo; errors are 404 unknown IDs, 409 stale previews or
cycles, and 422 invalid scope. Structured policy errors use `app/core/policy.py:105`.

## 11. Implementation steps

1. Add canonical fingerprints and stable IDs; test normalization, legacy determinism,
   insert/delete/reorder, and golden output (low risk; `app/core/fingerprint.py:1`).
2. Keep 0009 and registry data-safe; test SQLite/Postgres upgrade/downgrade and cycles
   (medium risk; `migrations/versions/0009_artifact_tables.py:1`).
3. Keep shadow registration write-only; test every stage and on/off output identity
   (medium risk; `app/services/artifact_shadow.py:29`).
4. Add invalidation/scope previews without skipping work; test language scoping,
   idempotence, and asset replacement (medium risk; `app/services/artifacts.py:55`).
5. Add edit intent, auditors, concurrency, and undo (high risk).
6. Enable hash-aware skipping only after loudness, PTS, and duration acceptance tests
   pass (high risk; renderer boundary `app/documentary/render/engine.py:563`).

## 12. Open risks and questions

Risks are coarse stage reuse, missing public fingerprints for all config/identity/voice/
music inputs, external output deletion, and no final-render loudness auditor
(`app/documentary/pipeline/base.py:94`, `app/documentary/render/engine.py:575`).
Partial rebuild is unsafe until those inputs are explicit. Questions are LUFS/duration
tolerances, export/import ID survival, and automatic language-scope widening.
