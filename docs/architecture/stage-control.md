# Stage-control backend contract

## Existing boundary

The stage status model is `DocumentaryJob.stages_json` and the public serializer
is `app/documentary/jobs.py:178-193`; execution status transitions live in
`app/documentary/pipeline/base.py:92-217`. Existing approval/generation APIs are
specialized: naming generation/approval is in `app/naming/api.py:112-142`,
while master approval is an internal service at `app/documentary/jobs.py:76-115`.
There was no shared documentary edit or regenerate endpoint. Pending user-edit
locks are checked by `app/services/skip_policy.py:143-148`; the existing read-only
scope endpoint is `app/documentary/api.py:64-71`.

## State/action contract

| action | available when | effect |
|---|---|---|
| `continue` | stage is not running and no pending edit lock | queue the exact predicted scope |
| `redo_with_prompt` | stage is not running and no pending edit lock | queue the exact predicted scope and retain the prompt in the audit record |
| `edit` | stage is not running | apply the submitted edit, then queue only its predicted downstream scope |
| `run_automatically` | always | persist per-case/per-stage `auto_continue` mode |

`GET /api/documentary/{case_id}/stage-control/{stage}` returns the status,
available actions, blocked reasons, automatic mode, and artifact state.
`POST .../stage-control/preview` stores an `EditLog` preview containing the
artifact ids, input/output hashes, predicted scope, and estimated calls. The
shared predictor is `app/services/soundness.py:349-365`.
`POST .../stage-control/apply` refuses changed hashes or pending user edits,
marks only the stored scope stale, and queues the runner with the preview's
artifact ids, stage names, and languages. The runner executes only that
explicit scope. Its result reports `previewed_artifact_ids`,
`executed_artifact_ids`, and `changed_outside_scope`; a non-empty outside set
fails the job and writes an audit event. `POST .../stage-control/automatic`
persists the mode in `AppSetting`.

`GET .../stage-control/result/{preview_id}` exposes the durable runner result;
`GET .../stage-control/audit` exposes `STAGE_CONTROL_*` events for the case
timeline. The UI uses one reusable `StageList`/`StageCard`/`ActionPanel` /
`PreviewDialog` set in `frontend/components/documentary/stage-control.tsx`.
Preview items with `may_change=true` are conservative/unproven scope members;
the UI labels them before apply. The result view requires
`changed_outside_scope=[]` and reports a failure otherwise.

The preview is the binding: apply cannot silently expand it. A German edit can
therefore only execute the German stages and preview-listed dependents; shared
artifacts execute only when explicitly present in the preview.

## UI editor paths

| artifact type | editor opened by ActionPanel | save path |
|---|---|---|
| `spoken_version` | spoken chapter text | edit → preview → apply |
| `chapter_plan`, `chapter_cards`, `timeline_cards` | chapter-card editor | edit → preview → apply |
| `audio_blocks` | ActionPanel opens the documentary audio-plan editor; free-text editing is disabled | editor integration remains a documented gap |
| `naming` | ActionPanel opens the case Naming editor; title approval uses case-control preview/apply | custom-title save remains on the legacy editor path |
| `visual_asset` | ActionPanel opens the case Thumbnail editor; approval/rejection uses case-control preview/apply | compose save remains on the legacy editor path |

The case workspace's Pipeline tab is the single stage-control surface. It selects
the latest run by default and can select another run for the same case. The Run
tab no longer renders a second control list. Naming, thumbnail, blueprint and
music tabs remain read/edit views for their domain data; their pipeline action is
represented by the corresponding artifact card in the case-level ActionPanel.
Case-level title approval, thumbnail approval/rejection, and localization
regeneration use `POST /api/cases/{case_id}/control/preview` followed by
`POST /api/cases/{case_id}/control/apply`; the preview binds the target content
hash and the apply result reports an empty outside-scope set. Structured editor
saves not listed as preview/apply above are intentionally retained as a
follow-up gap and are not represented as free-text stage edits.

## Pipeline map

The case view renders the complete ordered map, grouped as Story, Audio, Visual,
Render, and Shorts. It includes research, master story, approval, blueprint,
chapters, naming, audio plan, spoken/voice/performance/mix, visual planning and
pieces, production script, intro, host, music, captions, final render, and the
Shorts boundary. A run may have fewer enabled stages: the deterministic isolated
fixture has 10 enabled stages (research, master story, four spoken stages, and
four voice stages); represented or unavailable stages remain visible in the map
with an honest explanation and no executable action.

On narrow screens each card is one row and starts collapsed. The audit timeline
also starts collapsed. Language chips are deduplicated and language filtering
applies to the map. A preview marks the selected stage's artifacts “will change”
and conservative downstream artifacts “may change”; that distinction is shown
before Apply.
