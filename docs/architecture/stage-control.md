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
marks only the stored scope stale, queues the existing job runner, and writes a
policy audit event. `POST .../stage-control/automatic` persists the mode in
`AppSetting`.

The preview is the binding: apply cannot silently expand it. The existing job
runner may still conservatively rebuild unproven downstream types, as required
by the incremental policy; the control service never overwrites a user edit.
