# Shorts audit

Audit recorded at commit `e8a72d1208f468b3130ae780b8b24338d578d582` before the
10F repair changes. The click-through test is
`frontend/e2e/shorts-audit.spec.ts`; it runs with the isolated Playwright API
fixtures and no outbound network.

## Backend and storage

| capability | result | evidence |
|---|---|---|
| Planning / selecting moments | Pilot code exists: deterministic beat extraction, candidate construction, gates, and MMR selection. The candidate API does not persist by default. | `app/shortform/director.py:43-121`, `app/shortform/selection.py:21-155`, `app/shortform/api.py:53-67`, `app/shortform/director.py:94-103` |
| Script / captions | No production script producer. `ShortFormScript` is only a table declaration; the local exporter writes an SRT from supplied narration. | `app/db/models.py:1113-1126`, `app/shortform/export.py:80-91` |
| Vertical render | Offline local pilot export only: PNG cover, SRT, and an FFmpeg 1080x1920 MP4. | `app/shortform/export.py:18-27`, `app/shortform/export.py:94-165` |
| Per-language variants | Data/contract helpers exist, but no job/API pipeline persists or renders all languages. The pilot export call is English-only. | `app/shortform/operations.py:77-97`, `app/db/models.py:1129-1150`, `scripts/live_pilot_case6.py:320-364` |
| Automatic start after long film | Not implemented. The proposed trigger is explicitly design text, not a runner hook. No Shorts reference exists under `app/documentary` or its jobs. | `docs/architecture/shorts-and-publishing.md:54-66`; search result: no production Shorts hook |
| Status model | Table columns have draft/review-like status strings and publishing transitions are an offline contract; there is no short-render/job status API. | `app/db/models.py:1109`, `app/db/models.py:1125`, `app/db/models.py:1149`, `app/shortform/operations.py:54-70` |

Persisted pilot metadata is in `short_form_concepts`, `short_form_scripts`,
`platform_variants`, `short_form_publishing_plans`, and `short_form_metrics`.
The local export is a directory containing `cover.png`, `subtitles.srt`,
`short.mp4`, and `manifest.json`, under the caller-supplied output directory;
the pilot defaults to `reports/shortform/live_pilot_case6` and creates
`<platform>/<index>_<candidate-id>/`. Evidence: `app/db/models.py:1087-1207`,
`app/shortform/export.py:130-164`, `scripts/live_pilot_case6.py:376-376`.
These tables and files are not registered in the long-form artifact graph:
`app/core/artifact_dependencies.py:16-80` contains no Shorts artifact types.
No automatic production occurs today; the API candidate route is a read-only
pilot call and the FFmpeg export is invoked by the standalone pilot script.

## Every Shorts UI control

| view / control | handler and file:line | API call | route exists | audit result |
|---|---|---|---|---|
| Sidebar Short-Form link | Next navigation, `frontend/components/sidebar.tsx:36` | none | n/a | works |
| Workspace tabs: Candidates, Approved, YouTube Shorts, Instagram Reels, Facebook Reels, TikTok, Settings, Publishing Plan, Performance, Test Report | `TabBar` state change, `frontend/app/short-form/page.tsx:19,44-55` | none except Performance/Settings data | n/a | works |
| Regenerate batch | `onNotice`, `frontend/app/short-form/page.tsx:62` | none | no | does nothing beyond notice |
| Candidate header Edit | `onEdit` notice, `frontend/app/short-form/page.tsx:65` | none | no | does nothing beyond notice |
| Candidate Approve | local `approve`, `frontend/app/short-form/page.tsx:39,66` | none | no | local-only state |
| Candidate Edit | `onEdit` notice, `frontend/app/short-form/page.tsx:66` | none | no | does nothing beyond notice |
| Candidate Regenerate | `onEdit` notice, `frontend/app/short-form/page.tsx:66` | none | no | does nothing beyond notice |
| Candidate Reject | local `reject`, `frontend/app/short-form/page.tsx:40,66` | none | no | local-only state |
| Settings enabled checkbox and count inputs | local `setSettings`, `frontend/app/short-form/page.tsx:74` | none until Save | n/a | local form works |
| Settings Save | `save`, `frontend/app/short-form/page.tsx:74` | `PATCH /api/short-form/settings` | yes, `app/shortform/api.py:37-50` | works |
| Performance load | `useApi`, `frontend/app/short-form/page.tsx:73` | `GET /api/short-form/metrics` | yes, `app/shortform/api.py:32-35` | works |
| Review Back link | Next link, `frontend/app/short-form/review/page.tsx:17` | none | n/a | works |
| Review Caption, Destination, CTA fields | local React state, `frontend/app/short-form/review/page.tsx:34-37` | none | no | local-only state |
| Review Approve / Reject | local `setStatus`, `frontend/app/short-form/review/page.tsx:39` | none | no | local-only state |

## Isolated click-through result

The test clicked every actionable control above. “Local-only state” means the
visible control changes React state but creates no persisted backend effect;
“does nothing beyond notice” means it only renders a notice. No click returned
an HTTP error. The only backend calls observed were the initial settings GET,
the settings PATCH from Save, and the metrics GET.

| control group | result | observed API |
|---|---|---|
| navigation/tabs/platform previews | works | none |
| candidate regenerate/edit | works only as local notice; no persisted action | none |
| candidate approve/reject | works only as local state; no persisted action | none |
| settings controls + Save | works | `PATCH /api/short-form/settings` |
| Performance | works | `GET /api/short-form/metrics` |
| review fields | works only as local state | none |
| review approve/reject | works only as local state | none |

The recorded Playwright result was one passing test. Its exact control inventory
was: Regenerate batch `works (local UI)` / no API; header Edit `works (local UI)` /
no API; Approve `works (local UI)` / no API; Edit `works (local UI)` / no API;
Regenerate `works (local UI)` / no API; Reject `works (local UI)` / no API;
all six visited tabs `works (local UI)` / no API; Settings Save `works` /
`PATCH /api/short-form/settings`; Performance `works` /
`GET /api/short-form/metrics` (observed twice due to the view's data load);
Review Approve and Reject `works (local UI)` / no API.

This audit is the baseline for the repair; the dead controls must be disabled
with “Not implemented yet” until their real backend path exists, rather than
being presented as persisted workflow actions.

## Repair-state rerun

After the audit commit, the candidate and review-only actions were removed or
disabled. The rerun still passed all four Playwright tests: 3 existing
short-form tests plus the audit test. The final audit test recorded candidate
Regenerate batch, candidate header Edit, Approve, Edit, Regenerate, and Reject
as `removed (not implemented yet)`; Review Caption, Destination, CTA, Approve,
and Reject as `disabled (not implemented yet)`; Settings Save as working via
`PATCH /api/short-form/settings`; and Performance as working via
`GET /api/short-form/metrics` (observed twice during view loading). This makes
the unavailable pilot boundary visible without leaving a clickable dead action.

## Stage-control boundary

The Shorts workspace now renders the shared `StageList` with one selection stage
and per-language script/caption and vertical-render stages. Each card is visibly
not available rather than hidden; no preview or execution is offered because
there is no Shorts runner or artifact scope to preview. The long film therefore
cannot be blocked by Shorts, and no automatic start is claimed. The intended
future contract is per-language start only after a completed long-film render,
with isolated short failures.
