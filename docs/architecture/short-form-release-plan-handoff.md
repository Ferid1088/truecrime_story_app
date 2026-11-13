# Short-form release plan: agent handoff

This file is the implementation handoff for the episode-level Shorts/Reels release-plan work. Read it before extending the campaign UI, release API, social-account configuration, or publisher worker.

Related references:

- [Shorts and publishing architecture](shorts-and-publishing.md) — broad inventory and original production/publishing proposal.
- [Short-form engine build spec](../TRUECRIME%20SHORT-FORM%20ENGINE%20%E2%80%94%20BUILD%20SPEC.MD) — editorial, language, quality, and render design.
- [Host-led Shorts checklist](../shorts_host_led_checklist.md) — current status of avatar-led hooks, covers, outros, music, and disclosure work.
- [Social distribution config](../../config/social_distribution.json) — current schedule template and channel placeholders.

## 1. Product goal

Create one release document for one finished episode/campaign. The document contains a set of short creative concepts, their available language-specific renders, and one calendar for all enabled platforms.

The intended behavior is:

1. The approved English story is the canonical source for the short concepts and English master scripts.
2. Language agents transcreate the five scripts from that English material. They preserve evidence and claims while adapting hook, cadence, colloquial phrasing, punctuation, captions, cover text, and avatar delivery to each language.
3. Each short concept has one global include checkbox. It controls all its available language versions and all scheduled platforms. It is checked by default.
4. The owner reviews the configured reference language (currently `en`) and approves one release document.
5. A scheduler publishes each enabled language render to the mapped account/platform at its configured slot. Unchecking a short before plan approval removes its placements. Once a schedule is activated, unchecking it should cancel only that short's future placements; already published items and other shorts' dates remain unchanged.

One localized video file is reused across the four platforms for that language. The application must not create one rendered video per platform unless a measured platform requirement later proves that a variant is needed. Language versions are distinct videos because narration, avatar lip movement, hook, and copy are localized.

## 2. Changes currently in the codebase

### Configuration

`config/social_distribution.json` is a new, separate runtime config file. It contains:

- Campaign timezone (`Europe/Berlin`), reference review language (`en`), default local posting time (`18:00`), and default checked state.
- Enabled flags for YouTube Shorts, Instagram Reels, Facebook Reels, and TikTok.
- A ten-day schedule template. Each row assigns one numbered short to a pair of platforms. A short is assigned once to each of the four platforms across the two rows for that short.
- Per-language placeholders for `account_id`, public `channel_url`, and `credential_ref` for each platform.

The short-form release-plan service reads this file when it builds a plan. Keep OAuth access/refresh tokens and app secrets out of this file and out of Git. `credential_ref` is only a placeholder/reference field today; there is no account connection or secure token store implemented yet.

The platform/language account values are blank. Do not invent channel IDs or enable live posting until the owner supplies account mappings and completes OAuth setup.

### Backend

`app/shortform/release_plan.py` builds an episode release document from existing short candidates and rendered-short rows.

- It anchors to the latest completed documentary job for the case.
- If that job belongs to an Autopilot run, it gathers the completed language jobs in that same run. If no run is found, it falls back to the same non-empty `batch_id`; otherwise it uses only the anchor job.
- It groups candidate rows by `stable_key` to represent one creative across the available language renders. The English candidate is used as the representative when present.
- It creates date/platform/language slots from the config schedule. When a candidate is unchecked, its slots disappear without renumbering the remaining candidates.
- If the configured local posting time has already passed today, the default start moves to tomorrow. A past start date is rejected.
- It records slot readiness (`render_not_ready`, `account_not_connected`, or `configured`) for display. `configured` means a config reference exists; it does **not** mean OAuth works.
- `can_publish` is intentionally always false while publisher integration is absent.

`app/shortform/api.py` adds:

- `GET /api/short-form/{case_id}/release-plan` — preview from current renders, or return the saved plan for the current anchor job if its language-job set still matches.
- `POST /api/short-form/{case_id}/release-plan` — store/update a draft in the existing `ShortFormPublishingPlan` table. The body has `included_candidate_ids` and optional `start_date`.
- `POST /api/short-form/{case_id}/release-plan/{plan_id}/approve` — record owner approval after checking that every included creative has a completed and approved render in the configured reference language.

The approval endpoint records a plan approval only. It does not queue an upload or publish anything. The model's `source_episode_id` currently stores the anchor `DocumentaryJob.id`; preserve this convention unless a proper episode/campaign foreign key is introduced.

No database migration was added or run. `ShortFormPublishingPlan` already exists in the current schema/model.

### Frontend

- `frontend/app/short-form/release-plan/page.tsx` is the new single-document campaign screen. It shows language renditions, a reference-language preview, one checkbox per creative, the date selector, and the platform/language calendar.
- `frontend/app/short-form/page.tsx` adds a Release Plan link. The link is shown only when the release-plan API responds; this keeps the link hidden while an older backend process still returns 404.
- `frontend/lib/api.ts` contains the release-plan API calls.
- The page explicitly says that approval does not upload. Do not relabel the approval action as “published” or “started” until a real scheduler and publisher are wired in.

## 3. What is not implemented

This is a reviewable plan builder, **not an end-to-end autopublisher**. The following are still required:

1. **Canonical five-script generation.** The current short production path does not yet implement the discussed sequence of five English master scripts followed by batched language transcreation. It still produces shorts from the existing per-language film pipeline. Keep this separate from the release-plan work so creative generation can be developed and reviewed without changing publishing state.
2. **Stable cross-language creative identity.** Grouping currently relies on `ShortCandidate.stable_key`. Verify that keys are identical across languages for the same concept. If they are not, add an explicit language-independent creative/concept key; do not guess a match from title text alone.
3. **Per-language readiness gates.** The owner manually approves the reference language only. Before scheduling another locale, require a successful localized script/render and automated checks for names, dates, claims, language quality, and disclosure. A missing or failed locale should be visibly skipped/blocked; it must not silently pass.
4. **Account connection.** Add a proper “Connect account” OAuth flow and secure token storage/refresh/revocation. Config should hold channel identity and non-secret settings; the secret store should hold credentials. Validate that the authenticated account matches configured `account_id`/`channel_url` before a campaign can be activated.
5. **Real publishers.** `app/shortform/publishers.py` still contains non-posting stubs. Implement and separately validate YouTube, Instagram, Facebook, and TikTok adapters. Do not call the existing `Mark published` endpoint as a substitute for uploading; that endpoint only changes local bookkeeping/archive state.
6. **Durable upload outbox and scheduler.** Approval should materialize one idempotent task per `(campaign version, creative, language, platform, account)`. A publishing worker should claim due tasks with leases, upload, store the remote post ID/status, poll processing where needed, and retry transient errors with bounded backoff. This worker must not block or mutate long-film/short-render jobs.
7. **Pause, resume, edit, and cancellation semantics.** Add campaign pause/resume. Before activation, editing the calendar can rebuild it. After activation, removing a creative cancels only its unpublished tasks; published tasks remain historical facts. Do not move other posts automatically after activation.
8. **Real duplicate protection.** Enforce a unique idempotency key per destination account and slot. Reposting the same media to different platforms is expected. Reposting the same creative to the same account should be blocked unless the owner deliberately creates a new campaign/version and confirms the duplicate.
9. **Configuration validation/UI.** Validate time zone, template coverage, platform names, account language mappings, duplicate schedule rows, and enabled-platform coverage. Show missing account mappings before activation. Keep the campaign snapshot in the saved plan so later config edits do not silently rewrite an approved schedule.
10. **Publishing tests and live-safe dry run.** Add unit/integration coverage for plan grouping, checkbox behavior, stable dates, idempotency, cancellation, and adapters before connecting real accounts. A dry run must never contact a publish endpoint.

## 4. Platform constraints to preserve

- YouTube Data API can upload a video and set `status.publishAt` for scheduled publication; scheduled videos must satisfy YouTube's private/unpublished requirements.
- Meta publishing requires correctly mapped Instagram Professional/Creator/Business and Facebook Page accounts with the app permissions/tokens those APIs require.
- TikTok Direct Post requires querying creator settings, honoring available privacy options, and explicit user consent for the upload. TikTok's developer guidance also requires the app/client to pass its applicable review before public posting is available. Do not promise a server-only hands-off timer for TikTok until the approved product flow supports it.

Use the official docs when implementing adapters because API contracts and review requirements change:

- [YouTube `videos.insert`](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert)
- [Meta Instagram API collection](https://www.postman.com/meta/instagram/collection/6yqw8pt/instagram-api)
- [TikTok Direct Post reference](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)
- [TikTok Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines)

## 5. Safe development notes

- At the time this handoff was written, the local app had a long-form render active, and the running Uvicorn process was started without `--reload`. No service, worker, render, or database migration was stopped/restarted/run for these source changes.
- The new backend endpoints are present in source but are not available in that already-running Uvicorn process until a safe, normal backend restart. Do not restart it while a documentary render is active. The UI capability check hides the Release Plan link until the backend endpoint responds.
- The frontend dev server may hot-reload source edits; this does not change the running film-render process.
- No tests were run for this handoff. Do not claim the release planner or publishing flow is verified. The only previous source hygiene check was `git diff --check`.
- Keep future publishing work additive and isolated from `DocumentaryJob` and `ShortProductionJob` state. An upload error must never stop, retry, cancel, or rewrite film production.

## 6. Suggested implementation order for the next agent

1. Read this handoff, the existing [shorts and publishing architecture](shorts-and-publishing.md), and current publisher stubs.
2. Confirm stable candidate grouping across all languages from one Autopilot run; introduce an explicit creative key if needed.
3. Complete and approve the language-generation/render pipeline before creating a publish outbox.
4. Add secure account connection and validated account mappings. Never put tokens in JSON/config files.
5. Implement the durable outbox and dry-run scheduler, with all external posting disabled by default.
6. Implement one platform adapter at a time, starting with an account sandbox/private mode; add idempotency, remote status tracking, retry limits, and explicit TikTok consent/review handling.
7. Only after tests pass and the owner has connected accounts should the campaign action enqueue real posts. The owner-facing action should be “Approve plan and schedule”; platform delivery statuses live in the same document, not in separate per-platform documents.

