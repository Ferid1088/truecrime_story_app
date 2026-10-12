# Incremental build and artifact graph

This document separates verified behaviour from the proposed incremental-build design. Current-code claims carry a `file:line` reference; `PROPOSED` and `UNVERIFIED` text is not a claim about the current implementation.

## 1. Purpose and non-goals

The purpose is to make a documentary rebuild addressable, auditable, resumable, and safe to preview before execution. Today, a job persists stage state in `DocumentaryJob.stages_json` and reruns unfinished work; it does not yet use a dependency graph to skip work (`app/documentary/pipeline/base.py:61-150`).

Non-goals for the current implementation: hash-aware skipping, partial rendering, edit-by-prompt execution, media reuse, user media, audio recovery, timeline policy, shorts, publishing, and splitting the large pipeline modules. Those are PROPOSED follow-up work.

## 2. Artifact inventory

The rows below describe the current storage when it exists and the PROPOSED stable graph type. A blank hash/cache cell means no verified current cache key was found.

| type | where stored today | stable ID today | inputs | produced by | consumed by | hash/cache today | reuse today |
|---|---|---|---|---|---|---|---|
| master_story | `story_versions` row | row id | case research and prompt | `app/documentary/jobs.py:244-260` | approval, blueprint | `StoryVersion.text_hash` | latest approved row |
| beats | `blueprints.blueprint_json` | no member ID | master text | blueprint agent, `app/documentary/jobs.py:266-285` | audio, visuals | blueprint story hash | latest non-invalid row |
| audio_blocks | `audio_plans.plan_json` | no member ID | blueprint | audio-plan agent, `app/documentary/jobs.py:290-300` | voice and production | no graph hash | latest row |
| spoken_version | `story_versions` rows | row id + language | master and language | spoken branch, `app/documentary/jobs.py:308-340` | performance | row text hash | latest branch row |
| voice_takes | voice/performance tables and files | database row ids | spoken text and voice settings | pipeline voice stages, `app/documentary/jobs.py:217-220` | production script | file metadata only | stage reuse |
| visual_needs | visual-needs records | database ids | blueprint beats | visual-needs stage | visual research | no graph hash | current job state |
| visual_shots | visual plan JSON | no member ID | visual needs and firewall | director/plan stages | production script | no graph hash | latest plan |
| visual_pieces | visual asset rows/files | asset and piece ids | source video and cuts | `app/documentary/visuals/pieces.py` | visual audit, render | source metadata | audited rows reused |
| visual_plan | plan JSON/production rows | no member ID | needs, assets, story order | `app/documentary/jobs.py:215-220` | production script | no graph hash | latest plan unless refresh |
| chapter_plan | chapter plan rows/JSON | row id | master/blueprint | chapter writer | cards and script | no graph hash | latest valid plan |
| chapter_cards | production script/cards JSON | no member ID | chapters and timeline | `app/documentary/chapters.py` | renderer | no graph hash | regenerated with script |
| timeline_cards | production script/cards JSON | no member ID | meaningful events | timeline/card code | renderer | no graph hash | regenerated with script |
| intro | `data/intros/<lang>/` files | path/fingerprint | channel identity and size | `app/documentary/intros.py:119-141` | renderer | intro fingerprint | cached per channel/size |
| music_cues | audio plan JSON | no member ID | audio plan | audio planner | production audio | no graph hash | stage reuse |
| captions | production script JSON/SRT | no member ID | narration timings and text | production script and `render/engine.py:554-557` | renderer | no graph hash | regenerated with script |
| production_script | `production_scripts` rows | row id + language | spoken, voice, visuals, cards | `app/documentary/production/script.py:1347-1362` | critique and render | row/version fields | latest row |
| final_render | relative media path + production row | row id/path | production script, audio, intro | `app/documentary/render/engine.py:525-640` | UI/download | no graph hash | output file reused only by stage state |

PROPOSED: Task 6 adds stable member IDs and an `artifacts`/`artifact_deps` graph without making existing readers positional-incompatible.

## 3. Pipeline stage sequence and resume

`plan_stages()` creates these exact `stages_json[].name` keys (`app/documentary/jobs.py:208-221`): `research`, `master_story` (from-zero only), `master_approval`, `blueprint`, `audio_plan`, `spoken:<lang>`, optional `host_plan`, optional `chapters`, optional `naming`, `film_length`, `visual_needs`, `visual_research`, `visual_check`, `visual_plan`, `visual_gaps`, then per language `host:<lang>` (optional), `performance:<lang>`, `voice:<lang>`, `production:<lang>`, `critique:<lang>`, `visual_audit:<lang>`, `render:<lang>`.

Inputs and outputs are stage detail dictionaries saved in the matching entry: research ingests source/fact rows; master story writes a story version; approval chooses the approved master; blueprint writes a blueprint row; audio plan writes an audio-plan row; spoken writes one localized story version per language; visual stages write needs/assets/plans; performance and voice write audio rows/files; production writes a production script; critique and visual audit write verdicts; render writes the final relative path and media metadata (`app/documentary/jobs.py:241-300`, `app/documentary/pipeline/base.py:94-150`).

On resume, `StageRunner` skips entries whose status is `done`, `skipped`, or `degraded`; failed/pending entries run again and their attempts/errors remain in the JSON (`app/documentary/pipeline/base.py:94-150`). The explicit resume endpoint resets a failed/partial/interrupted/cancelled job’s active stage entries to pending (`app/documentary/api.py:498-518`). This is stage-level resume, not member-level resume.

## 4. Final render today and per-segment analysis

Current render reads a production script, resolves narration audio, writes an SRT, streams RGB frames to ffmpeg, maps AAC audio and mov_text subtitles, and atomically renames the `.part` output (`app/documentary/render/engine.py:525-580`, `616-640`). Frames are selected from shot windows, crossfaded or faded to black at transition points, overlaid with cards/subtitles, and faded at the end (`app/documentary/render/engine.py:581-613`).

When an intro is present, the graph adds intro audio, delays it, ducks film audio with volume ramps, and maps mixed audio; otherwise it maps documentary audio directly (`app/documentary/render/engine.py:477-501`, `568-578`). Intro files are made/cached by language, size, and fingerprint (`app/documentary/intros.py:24-27`, `119-141`). The renderer currently does not apply a separate `loudnorm` filter or crossfade audio segments; loudness is inherited from prepared audio. This is a verified limitation.

Per-segment rendering is PROPOSED. A segment stitch must preserve PTS, continuous narration sample timing, the global loudness target, intro ducking windows, transition overlaps, subtitle offsets, and chapter/timeline timing. Risks are encoder delay, cumulative duration drift, reset audio clocks, duplicated loudness normalization, and a crossfade consuming the next segment’s first words. Acceptance should measure integrated loudness within tolerance, total duration drift below a threshold, no audio discontinuity at joins, and subtitle timestamps within their owning segment.

PROPOSED stitch: render each segment with a shared time base and codec parameters, retain exact input PTS/durations, concatenate only after validating no overlap/gap, apply loudness once to the assembled program, then run a duration/PTS/loudness/discontinuity auditor.

## 5. Dependency table

The following is the declarative PROPOSED registry table used by Task 6. “Invalidates” is the transitive downstream set, not a current skip rule.

| artifact | depends on | invalidates |
|---|---|---|
| master_story | research inputs, prompts, model/config | beats, audio_blocks, spoken_version, visual_needs, chapters, production_script, final_render |
| beats | master_story | audio_blocks, visual_needs, visual_shots, chapter_plan, timeline_cards, captions, production_script, final_render |
| audio_blocks | beats, audio config | voice_takes, spoken_version, music_cues, captions, production_script, final_render |
| voice_takes | audio_blocks, spoken_version, voice config | production_script, captions, final_render |
| spoken_version | master_story, language, localization config | voice_takes, captions, production_script, final_render |
| visual_needs | beats, spoiler firewall | visual_shots, visual_pieces, visual_plan, production_script, final_render |
| visual_shots | visual_needs, assets, story order | visual_plan, production_script, final_render |
| visual_pieces | source media, cut window, auditor result | visual_plan, production_script, final_render |
| visual_plan | visual_needs, visual_shots, visual_pieces | production_script, final_render |
| chapter_plan | master_story, beats, chapter config | chapter_cards, captions, production_script, final_render |
| chapter_cards | chapter_plan, timeline_cards | production_script, final_render |
| timeline_cards | beats, meaningful events, language layout | production_script, final_render |
| intro | channel identity, language, size | final_render |
| music_cues | audio_blocks, music config | production_script, final_render |
| captions | spoken_version, voice_takes, timing | production_script, final_render |
| production_script | spoken_version, voice_takes, visual_plan, cards, captions, music_cues | critique, final_render |
| final_render | production_script, audio, intro, render config | none |

A timing change in one audio block therefore invalidates the dependent voice take and every timing-bearing visual, card, timeline, caption, music, production script, and final render in that language. Shorts are downstream only after a finished long film and cannot invalidate or block it (final decision).

## 6. Artifact-graph schema

PROPOSED schema (implemented by migration 0009): `artifacts(id String primary key, case_id Integer nullable, production_id Integer nullable, language String nullable, type String, scope_json Text, input_hash String, output_hash String nullable, output_ref String, status String, version Integer, created_by String, edit_id String nullable, stage_job_id Integer nullable, created_at DateTime, updated_at DateTime)`; `artifact_deps(artifact_id String, depends_on_id String, unique(artifact_id, depends_on_id))`; and `edit_log(id Integer primary key, created_at DateTime, actor String nullable, scope_type String nullable, scope_id String nullable, instruction_text Text, intent_json Text, scope_preview_json Text, status String, result_json Text, parent_edit_id Integer nullable)`.

Indexes are required on artifact case/type/language/status, both edge directions, and edit creation/scope. Status state machine: `fresh → stale → building → fresh`, with `building → failed|omitted`; a rejected edit does not alter artifact status. Version increments only when an unchanged ID receives changed inputs.

The graph is separate from `DocumentaryJob.stages_json`: stages remain the operational resume record (`app/db/models.py:1196-1204`); graph rows are addressable outputs. Existing `override_log` and `policy_events` remain audit records (`app/db/models.py:1667-1699`); `edit_log` records edit intent and lifecycle and does not duplicate policy decisions.

## 7. ID scheme

PROPOSED new members use UUID-based IDs with a type prefix, for example `beat_01J...`, `shot_01J...`, `chapter_card_01J...`, `timeline_card_01J...`, and `caption_01J...`. IDs are assigned at creation, persisted in JSON, never positional, and never derived from text. A legacy member without an ID receives deterministic UUID5 from `(parent_id, member_type, original_position)` and `legacy_id: true` on the next in-memory load; historical rows are not rewritten unless their stage is saved.

## 8. Invalidation algorithm

```text
invalidate(requested_ids, reason):
  affected = ordered set(requested_ids); queue = requested_ids
  while queue:
    id = queue.pop()
    if id is visiting: raise CycleError
    mark id visiting
    for dependent in reverse_edges(id):
      if dependent not in affected: add dependent and queue it
    mark id stale and visiting complete
  persist each state transition once with reason
  return affected grouped by type
```

PROPOSED cycle detection uses a color set over the full reachable subgraph before committing; repeated invalidation of the same IDs is idempotent because stale rows and the same reason produce no new transition.

## 9. Edit-by-prompt flow

PROPOSED intent schema: `{"instruction":"string","scope":{"language":"en|null","beat_ids":["beat_..."]},"requested_changes":[{"field":"string","value":{}}],"reason":"string"}`. The policy layer validates allowed fields and scope, previews dependencies, and escalates story/fact/spoiler changes to the relevant auditors. If the requested field is outside the selected scope, the UI asks for approval to widen the scope. Preview is `{rebuilds:N,total:M,affected_by_type:{},estimated_cost_usd:null,estimated_time_seconds:null}`. Execution records before/after hashes, runs auditors, and offers accept/redo/undo. A redo creates a child edit; undo marks the edit undone and invalidates affected outputs again. If a job is running, preview is allowed, execution queues behind it or explicitly cancels/replans; optimistic input hashes reject stale previews.

## 10. API endpoints

PROPOSED: `GET /api/documentary/{case_id}/artifacts?type=&language=&status=` returns artifact summaries; `POST /api/documentary/{case_id}/artifacts/scope-preview` with `{artifact_ids:[]}` returns grouped counts and `N of M`; `POST /api/documentary/{case_id}/edits/preview` and `/execute` return an edit record and before/after hashes; `POST /api/documentary/{case_id}/edits/{id}/redo` and `/undo` return updated status. Errors are `404` unknown case/artifact, `409` stale preview or cycle, `422` invalid intent/scope, and structured policy errors for auditor rejection.

## 11. Implementation steps

1. Add canonical fingerprints and stable member IDs; test normalization, legacy determinism, insert/delete/reorder (low risk).
2. Add migration 0009 and registry dependency data; test SQLite/Postgres schema and cycle detection (medium risk).
3. Add write-only shadow registration and read-only inspection endpoints; assert output identity with shadow on/off (medium risk).
4. Add invalidation and scope previews without skip behaviour; test transitive sets and idempotence (medium risk).
5. Add edit intent and auditor escalation; integration-test concurrency and undo (high risk).
6. Add hash-aware skipping only after acceptance metrics prove no timing/loudness regression (high risk).

## 12. Open risks and questions

Open risks: current JSON members lack stable IDs; output files can be externally removed; render timing is audio-driven; no current loudness auditor is in the final renderer; and current stage retry/resume is coarser than member invalidation (`app/documentary/pipeline/base.py:94-150`). Open questions: exact loudness tolerance, whether artifact IDs must survive case export/import, and whether an edit may widen from one language to all languages automatically.
