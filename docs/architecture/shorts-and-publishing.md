# Shorts and publishing

This document separates the existing short-form pilot from a **PROPOSED** production
and publishing system. No live publishing or OAuth is implemented here.

## 1. Purpose, non-goals, and final decisions

The purpose is to document the current pilot and the design needed after a long film is
complete. This task does not implement media reuse, shorts behaviour, publishing,
OAuth, or platform adapters.

Final decisions: one approval click per language approves a bundle containing the long
film and its shorts; items can be excluded or rejected independently; editing voids only
that item’s approval; approval is bound to content hashes; an unconnected language stays
approved but does nothing until “release now”; visibility is private/draft until live
mode is enabled for a channel; shorts start only after the long film is finished and a
short failure can never block the long film or its release.

## 2. Reality check

This inventory covers every source module under `app/shortform`, every thumbnail module
relevant to a short, YouTube metadata/upload-related lifecycle code, and both short-form
frontend pages. REAL means code is present and tested; MOCK means an offline provider
stub; STUB means a contract or pilot surface without the production workflow.

| surface | status | evidence | tests |
|---|---|---|---|
| `app/shortform/api.py` (`ShortFormSettingsPatch`, four routes) | REAL pilot API | `app/shortform/api.py:18`, `app/shortform/api.py:23` | `tests/test_shortform_phase1.py:1` |
| `app/shortform/assets.py` (`UnverifiedVisualAssetsError`, asset gates) | REAL | `app/shortform/assets.py:10` | `tests/test_shortform_asset_selection.py:1` |
| `app/shortform/director.py` (`BeatMaterial`, `ShortFormDirectorAgent`, helpers) | REAL candidate construction | `app/shortform/director.py:33`, `app/shortform/director.py:43` | `tests/test_shortform_phase2.py:1` |
| `app/shortform/export.py` (`SubtitleCue`, `build_export_package`) | REAL local export | `app/shortform/export.py:30`, `app/shortform/export.py:94` | `tests/test_shortform_phase4.py:1` |
| `app/shortform/gates.py` (modality/reveal/rights/disclosure/duration gates) | REAL deterministic gates | `app/shortform/gates.py:8`, `app/shortform/gates.py:60` | `tests/test_shortform_phase3.py:1` |
| `app/shortform/metrics.py` (`concept_type_comparison`) | REAL read model | `app/shortform/metrics.py:21` | `tests/test_shortform_phase4.py:1` |
| `app/shortform/operations.py` (`CriticDecision`, `PlatformExport`, `SocialPublisher`, planning) | STUB/offline contract | `app/shortform/operations.py:13`, `app/shortform/operations.py:41` | `tests/test_shortform_operations.py:1` |
| `app/shortform/publishers.py` (`PlatformPublisherStub`, four platform classes) | MOCK; no upload | `app/shortform/publishers.py:30`, `app/shortform/publishers.py:42` | `tests/test_shortform_operations.py:1` |
| `app/shortform/selection.py` (scoring/MMR/diversity) | REAL candidate selection | `app/shortform/selection.py:21`, `app/shortform/selection.py:105` | `tests/test_shortform_phase2.py:1` |
| `app/thumbnails/api.py` (list/options/compose/decision/image routes) | REAL thumbnail API | `app/thumbnails/api.py:56`, `app/thumbnails/api.py:88` | `tests/test_thumbnails.py:1` |
| `app/thumbnails/brief.py` (`BriefRefused`, `build_brief`, validation) | REAL | `app/thumbnails/brief.py:23`, `app/thumbnails/brief.py:37` | `tests/test_thumbnails.py:1` |
| `app/thumbnails/compose.py` (`compose_thumbnail`) | REAL local composition | `app/thumbnails/compose.py:83` | `tests/test_thumbnails.py:1` |
| `app/thumbnails/critic.py` (deterministic/vision/review) | REAL deterministic + mocked vision | `app/thumbnails/critic.py:42`, `app/thumbnails/critic.py:111` | `tests/test_thumbnails.py:1` |
| `app/thumbnails/hosts.py` (`HostAsset`, outfit/host selection) | REAL asset lookup | `app/thumbnails/hosts.py:35`, `app/thumbnails/hosts.py:94` | `tests/test_thumbnails.py:1` |
| `app/thumbnails/selection.py` (late-reveal/asset ranking) | REAL selection | `app/thumbnails/selection.py:31`, `app/thumbnails/selection.py:89` | `tests/test_thumbnails.py:1` |
| `app/thumbnails/service.py` (`ThumbnailRefused`, create/decide) | REAL persistence | `app/thumbnails/service.py:22`, `app/thumbnails/service.py:42` | `tests/test_thumbnails.py:1` |
| `app/lifecycle/videos.py` (YouTube title/metadata/register/publish) | REAL local metadata/state; no upload | `app/lifecycle/videos.py:59`, `app/lifecycle/videos.py:74`, `app/lifecycle/videos.py:191` | `tests/test_case_lifecycle.py:1` |
| `app/lifecycle/api.py` (film publish endpoint) | REAL local state API | `app/lifecycle/api.py:128`, `app/lifecycle/api.py:136` | `tests/test_case_lifecycle.py:1` |
| `frontend/app/short-form/page.tsx` | REAL pilot settings/review UI | `frontend/app/short-form/page.tsx:20`, `frontend/app/short-form/page.tsx:41` | `frontend/e2e/phase12-shortform.spec.ts:1` |
| `frontend/app/short-form/review/page.tsx` | REAL pilot evidence/export UI | `frontend/app/short-form/review/page.tsx:17` | `frontend/e2e/phase12-shortform.spec.ts:1` |

Current persisted short concepts, scripts, platform variants, publishing plans, and
metrics are not the final hash-bound release model (`app/db/models.py:1087`,
`app/db/models.py:1113`, `app/db/models.py:1168`). The publisher classes are mocks
(`app/shortform/publishers.py:30`).

## 3. Shorts pipeline design (PROPOSED)

The trigger is a completed long-film render for one language, extending the render result
boundary (`app/documentary/jobs.py:534`) and local exporter (`app/shortform/export.py:94`).
Select moments from long-film beats/captions with a hook, standalone sense, and a
spoiler firewall that reveals no later material. Extend current beat extraction and MMR
selection (`app/shortform/director.py:137`, `app/shortform/selection.py:134`).

Each candidate gets language-native copy, vertical reframing with safe zones, large-word
captions, and a per-short auditor. `subtitle_layout` is the current boundary; Persian and
Arabic need explicit RTL shaping/alignment, English and German remain LTR
(`app/shortform/export.py:36`). Long-film artifacts are reused read-only by hash. Any
short failure is isolated and cannot change long-film state.

Platform lengths, loudness, output formats, title/description/hashtag limits, and exact
caption rules are **UNVERIFIED** in this offline repository. The pilot has configured
ranges and disclosure checks only (`app/shortform/gates.py:151`, `app/shortform/gates.py:170`).

**PROPOSED full data model** (PostgreSQL types; nullable-safe SQLite equivalents):

| table | columns | constraints/indexes |
|---|---|---|
| `short_candidates` (extend `short_form_concepts`) | `id BIGINT`, `case_id BIGINT`, `long_artifact_id VARCHAR(180)`, `language VARCHAR(10)`, `source_beat_ids_json TEXT`, `evidence_ids_json TEXT`, `asset_ids_json TEXT`, `hook TEXT`, `body TEXT`, `cta TEXT`, `input_hash VARCHAR(64)`, `status VARCHAR(20)`, `excluded BOOLEAN`, `rejection_reason TEXT`, `auditor_json TEXT`, `created_at TIMESTAMP`, `updated_at TIMESTAMP` | PK; case FK; unique `(long_artifact_id, language, input_hash)`; index `(case_id, language, status)` |
| `short_renders` | `id BIGINT`, `candidate_id BIGINT`, `platform VARCHAR(40)`, `language VARCHAR(10)`, `input_hash VARCHAR(64)`, `output_hash VARCHAR(64)`, `output_ref TEXT`, `duration_seconds DOUBLE`, `width INTEGER`, `height INTEGER`, `status VARCHAR(20)`, `auditor_json TEXT`, `created_at TIMESTAMP` | PK; candidate FK; unique `(candidate_id, platform, input_hash)`; index `(platform, status)` |
| `publish_accounts` | `id BIGINT`, `channel VARCHAR(40)`, `platform VARCHAR(40)`, `language VARCHAR(10)`, `external_account_id VARCHAR(200)`, `live_mode BOOLEAN`, `visibility_default VARCHAR(20)`, `status VARCHAR(20)`, `created_at TIMESTAMP`, `updated_at TIMESTAMP` | PK; unique `(platform, external_account_id, language)`; index `(channel, language)` |
| `oauth_tokens` | `id BIGINT`, `account_id BIGINT`, `key_id VARCHAR(80)`, `ciphertext BYTEA`, `nonce BYTEA`, `associated_data BYTEA`, `expires_at TIMESTAMP`, `revoked_at TIMESTAMP NULL`, `created_at TIMESTAMP` | PK; account FK; index `(account_id, revoked_at)`; ciphertext only |
| `release_bundles` | `id BIGINT`, `case_id BIGINT`, `language VARCHAR(10)`, `long_render_id VARCHAR(180)`, `approval_hash VARCHAR(64)`, `status VARCHAR(20)`, `live_mode BOOLEAN`, `approved_at TIMESTAMP`, `created_at TIMESTAMP`, `updated_at TIMESTAMP` | PK; unique `(case_id, language, approval_hash)`; index `(case_id, language, status)` |
| `release_items` | `id BIGINT`, `bundle_id BIGINT`, `item_type VARCHAR(20)`, `render_id VARCHAR(180)`, `content_hash VARCHAR(64)`, `status VARCHAR(20)`, `excluded BOOLEAN`, `rejection_reason TEXT`, `voided_at TIMESTAMP NULL` | PK; bundle FK; unique `(bundle_id, item_type, render_id)`; index `(bundle_id, status)` |
| `publish_jobs` | `id BIGINT`, `release_item_id BIGINT`, `account_id BIGINT`, `idempotency_key VARCHAR(180)`, `status VARCHAR(20)`, `attempt_count INTEGER`, `next_attempt_at TIMESTAMP NULL`, `remote_id VARCHAR(200) NULL`, `error TEXT NULL`, `created_at TIMESTAMP`, `updated_at TIMESTAMP` | PK; item/account FKs; unique idempotency key; index `(status, next_attempt_at)` |
| `publish_events` | `id BIGINT`, `publish_job_id BIGINT`, `created_at TIMESTAMP`, `event_type VARCHAR(40)`, `from_state VARCHAR(20)`, `to_state VARCHAR(20)`, `remote_id VARCHAR(200) NULL`, `context_json TEXT` | PK; job FK; index `(publish_job_id, created_at)` |

## 4. Publishing design (PROPOSED)

Channel identity and language branding currently come from identity configuration and
intro lookup (`config/parameters/identity.json:2`, `app/documentary/intros.py:86`); account
mapping can extend that boundary. Each OAuth callback must bind state to channel,
language, and account.

Tokens must be encrypted at rest using AES-256-GCM or XChaCha20-Poly1305, with the key
from macOS Keychain or an external secret store, never this repository. Store a key ID
and rotate by decrypting old ciphertext and re-encrypting with the new key.

**PROPOSED** interface, replacing the current mock boundary (`app/shortform/publishers.py:30`):

```python
class Publisher(Protocol):
    async def upload(self, item, account, *, idempotency_key: str): ...
    async def poll(self, receipt): ...
    async def publish(self, receipt, *, visibility: str): ...
    async def delete(self, receipt): ...
```

Adapters are planned for YouTube long/Shorts, Instagram Reels, Facebook Reels, and
TikTok. They need idempotency, resumable uploads, bounded retries, quota errors, and
audit events. Default visibility is private/draft; live mode is explicit. Existing
YouTube code computes metadata and local state only (`app/lifecycle/videos.py:74`,
`app/lifecycle/videos.py:191`).

## 5. Approval and publishing state machine

States are `draft`, `in_review`, `approved`, `scheduled`, `uploading`, `published`,
`failed`, `rejected`, plus `excluded` and `voided`. One language approval captures the
long-film and included-short hashes. Editing voids only that item. Upload requires the
stored approval hash to equal the current render hash and excludes voided/rejected items.

| transition | trigger | invariant |
|---|---|---|
| draft → in_review | long render/candidate audit ready | all included hashes present |
| in_review → approved | one language approval click | bundle hash captured |
| in_review → rejected/excluded | reviewer decision | item reason stored |
| approved → voided | item edit | other bundle items remain approved |
| approved → scheduled | release schedule | hashes current |
| scheduled → uploading | worker claim | idempotency key unique |
| uploading → published | platform receipt | visibility policy applied |
| uploading → failed | terminal platform error | retry reason retained |

A language without a connected account stays approved and does nothing until “release
now”. Shorts begin only after the long film is finished; no short transition may block
the long film or its release.

## 6. Per-platform requirements

No official platform documentation is present in this repository. Every cell is therefore
**UNVERIFIED** and must be checked before adapter work.

| platform | API product/app review | audit/disclosure | rate limits/quota | format/length | OAuth/publish permissions |
|---|---|---|---|---|---|
| YouTube long + Shorts | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Instagram Reels | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Facebook Reels | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| TikTok | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |

## 7. UI design (PROPOSED)

Extend the current workspace and review page (`frontend/app/short-form/page.tsx:41`,
`frontend/app/short-form/review/page.tsx:17`) with a review queue, per-video all-language
bundle overview, one-click per-language approval, item exclude/reject/edit, auditor
reasons, content hashes, repair-by-prompt preview, live-mode switch, and explicit
release-now. Every screen handles all ten states and disconnected accounts. Keyboard
focus, non-colour state, captions, screen-reader labels, and `dir="rtl"` for Persian/
Arabic are required.

## 8. API endpoints (PROPOSED)

`GET /api/documentary/{case_id}/short-bundles`, `GET /api/shorts/{id}`, `POST
/api/shorts/{id}/exclude`, `POST /api/shorts/{id}/reject`, `POST /api/shorts/{id}/approve`,
`POST /api/shorts/{id}/edit-preview`, `POST /api/channels/{channel}/oauth/start`,
`GET /api/channels/{channel}/oauth/callback`, `POST /api/documentary/{case_id}/release-now`,
and `POST /api/channels/{channel}/live-mode` are **PROPOSED**. Responses include state,
hash, auditor result, account availability, visibility, and event ID. Errors are 409
stale hash, 422 invalid action, 429 quota, 503 unavailable account, and structured policy
errors (`app/core/policy.py:105`).

## 9. Implementation steps

1. Extend `ShortFormConcept` with hash-bound candidates/items and add the proposed tables;
   test per-language approval and item isolation (medium risk; `app/db/models.py:1087`).
2. Trigger shorts only after render and isolate failures; test long release is unchanged
   by rejected shorts (`app/documentary/jobs.py:560`) (high risk).
3. Add vertical rendering, RTL captions, copy, and a per-short auditor using current
   export/gate boundaries (`app/shortform/export.py:94`, `app/shortform/gates.py:170`).
4. Add encrypted accounts and OAuth adapters one at a time; security-test key rotation
   and callback binding (high risk).
5. Add resumable publishing, quotas, private mode, live mode, and review UI with mocked
   platform contract tests (high risk).

## 10. Open risks and questions

Risks are changing platform policy, OAuth review delays, RTL font shaping, remote
acceptance after upload, and metadata hash drift. Questions are the first platform, Mac
key-management boundary, live-mode actors, exact loudness/length tolerances, and whether
account mapping belongs in identity configuration or the database.
