# Shorts and publishing architecture

Status: regenerated from the current code on 2026-10-12. The short-form
models and deterministic export contracts exist, but end-to-end generation,
approval bundles, OAuth and platform posting are not complete.

## What is real today

- `ShortFormConcept`, `ShortFormScript`, `PlatformVariant`,
  `ShortFormPublishingPlan` and `ShortFormMetric` tables exist.
- `ShortFormDirectorAgent` can create candidate concepts from an editorial
  blueprint, and deterministic gates/selection code records gate results.
- The short-form settings, metrics and blueprint-candidate API routes exist.
- `shortform/export.py` can build a local deterministic export package and
  subtitle layout, subject to local ffmpeg availability.
- `ExportPackagePublisher` is the only working publisher and produces an
  export handoff; platform publisher classes deliberately raise a stub error.
- The current config explicitly keeps short-form status `prototype` and says
  it does not block long-form release.

## What is still a stub or missing

There is no production gate that waits for a finished long film before short
generation, no one-click per-language approval bundle, no item-level
exclusion/rejection lifecycle, no content-hash-bound approval record, and no
OAuth/token store or live platform API publisher. The current publishing
helpers use `review`/`publishing` spellings and a smaller transition set; the
final state machine below supersedes those names when publishing is built.
There is no live-mode UI or per-channel release queue. Platform requirements
remain UNVERIFIED until each official API contract and integration test is
reviewed.

## Short-form pipeline design

1. Finish and approve the long film for a language. Shorts are not eligible
   before that state. A long film remains releasable if a short is rejected,
   omitted or fails.
2. Derive candidate concepts only from approved long-film beats, evidence and
   assets. Store allowed/forbidden reveals and source IDs with every concept.
3. Write and audit native scripts per language. Reject a short item without
   changing the long film or other language.
4. Build platform variants and deterministic export packages. Validate rights,
   duration, layout, disclosure, CTA and content hashes.
5. Let the user exclude or reject individual items. An edit voids approval of
   that item only and creates a new content hash.
6. Keep the bundle status `prototype` until the complete shorts test suite and
   the production gates pass. A failed short is reported but never blocks
   long-film completion or release.

One approval click per language approves a bundle containing the long film and
its shorts. Approval is bound to the selected long-film and short-item content
hashes. A language without a connected publishing account remains approved,
but does nothing until the user presses **release now**.

## Publishing state machine

The target state machine is:

```text
draft -> in_review -> approved -> scheduled -> uploading -> published
   |         |            |           |            |
 rejected <-+------------+-----------+------------+----> failed
```

`draft` is the default for a new long film or short item. `in_review` means
the bundle or item is awaiting the user's decision. `approved` records the
per-language bundle approval and hashes. `scheduled` is a user-approved
release slot. `uploading` is an idempotent provider operation. `published`
records the remote URL and published hash. `failed` retains the provider
error and is retryable after review. `rejected` is terminal for that version
but a new edit can create a new draft. Individual items can be excluded
without rejecting the rest of the bundle.

Visibility is private/draft by default. Live mode is an explicit per-channel
user setting; only then may a release request choose public visibility.

## OAuth and token storage

Each channel/platform connection should store provider, account/channel ID,
scopes, encrypted access token, encrypted refresh token, expiry, and a
redacted connection status. Encryption keys come from deployment secret
management, not the database or logs. Tokens must never appear in policy
context, request logs, error text, exported bundles or frontend payloads.
Refresh is server-side, bounded by a timeout, and audited by connection ID.
Disconnect revokes/forgets the encrypted token material according to the
provider contract while retaining non-secret audit history.

## Publisher interface

The eventual interface should be explicit and idempotent:

```python
class Publisher(Protocol):
    def authorize(self, channel_id: str) -> ConnectionStatus: ...
    def validate(self, item: PublishItem) -> list[PolicyError]: ...
    def upload(self, item: PublishItem, idempotency_key: str) -> UploadResult: ...
    def status(self, remote_id: str) -> UploadStatus: ...
    def cancel(self, remote_id: str) -> None: ...
```

`ExportPackagePublisher` remains useful for local handoff and tests. Live
publishers must enforce the hash, approval, visibility and channel checks in
the service, not rely on UI state.

## Platform requirements — UNVERIFIED

| Platform | Required verification before implementation |
| --- | --- |
| YouTube Shorts | OAuth scopes, upload/resumable upload, privacy default, title/description limits, quota, Content ID/disclosure |
| Instagram Reels | Meta app review, Page/Instagram account linkage, media container lifecycle, publish permissions, rate limits |
| Facebook Reels | Page token scopes, upload lifecycle, privacy and policy requirements, rate limits |
| TikTok | Login Kit scopes, Content Posting API review, upload/direct-post rules, privacy/audience controls, disclosure |

No platform requirement in this table should be treated as implemented until
the official current documentation and a mocked contract test are recorded.

## UI design

The production screen should show one language bundle card: long-film hash,
short-item list, per-item status, exclusions/rejections, account connection,
visibility and release state. The user can open each intermediate script,
variant and export, edit by prompt, accept/redo/undo, or exclude one item.
The single language approval button is disabled when required audits fail and
shows the exact failed item IDs. A missing account leaves the bundle approved
and exposes **release now** after connection; it does not silently publish.
Live mode is a separate per-channel toggle with a clear private/draft default.
Long-film status and release controls are visually independent from the short
queue; short failures display warnings and cannot block them.

## Implementation steps, tests and risk

1. Add long-film-finished and short-isolated orchestration (high risk).
   Test that no short starts early and that failed/rejected shorts never alter
   long-film completion or release.
2. Add bundle/item approval tables with content hashes and override events
   (high risk). Test one click per language, item exclusion/rejection, and
   item-only approval invalidation after an edit.
3. Replace the prototype state helpers with the full state machine (medium
   risk). Test every legal transition, rejected/failed recovery, idempotency,
   and private-by-default visibility.
4. Add encrypted OAuth connections and mocked platform adapters (high risk).
   Test token secrecy, refresh expiry, timeout/retry, and no external calls in
   CI. Platform requirements remain UNVERIFIED until contract review.
5. Build the review/release UI and per-channel live-mode controls (medium
   risk). Test missing-account approval, explicit release-now, and isolated
   short failure display.
6. Promote status from `prototype` only after backend, frontend and mocked
   end-to-end tests pass (low operational risk, high release significance).
