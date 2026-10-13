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
