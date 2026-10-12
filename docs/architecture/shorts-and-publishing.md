# Shorts and publishing

## 1. Purpose, non-goals, and final decisions

This document records the current short-form pilot and the PROPOSED production/publishing architecture. It does not implement publishing, OAuth, token storage, or a shorts generator.

Final decisions: one approval click per language approves a bundle containing the long film and its shorts; items can be excluded or rejected independently; editing voids approval of that item only; approval is bound to content hashes; an unconnected language account remains approved but does nothing until “release now”; visibility is private/draft until live mode is enabled per channel; shorts start only after the long film is finished and a short failure can never block the long film or its release.

## 2. Reality check

| surface | status | verified behaviour | tests |
|---|---|---|---|
| `app/shortform/selection.py` | REAL pilot | selects candidate beats and applies configured ranges | short-form tests |
| `app/shortform/director.py` | REAL pilot | creates candidates from case artifacts and gates | short-form tests |
| `app/shortform/gates.py` | REAL pilot | reveal/repetition/verification gates | short-form tests |
| `app/shortform/export.py` | REAL local export | deterministic local export contract | short-form tests |
| `app/shortform/operations.py` | STUB contract | planning data structures, no release workflow | short-form tests |
| `app/shortform/publishers.py` | STUB contract | publisher interface/no live adapters | short-form tests |
| `GET /api/short-form/settings` | REAL | returns configured pilot settings | API tests |
| `PATCH /api/short-form/settings` | REAL | updates pilot settings | API tests |
| `GET /api/short-form/metrics` | REAL | reads pilot metrics | API tests |
| `GET /api/short-form/blueprints/{id}/candidates` | REAL | returns candidate table | API tests |
| `/short-form` | REAL pilot UI | settings, metrics, evidence boundary | frontend e2e |
| `/short-form/review` | REAL pilot UI | candidate review surface | frontend e2e |
| OAuth/token storage/live upload | MISSING | no verified implementation found | none |

The UI reports zero persisted concepts and zero original-audio exports in its pilot report (`frontend/app/short-form/page.tsx:74`); “REAL” here does not mean production-ready publishing.

## 3. Shorts pipeline design (PROPOSED)

Start only after the long-film job reaches finished render for that language. Select moments from approved long-film beats: each moment needs a hook, standalone sense, and a spoiler firewall that does not reveal beyond the short. Selection is per language and uses spoken/caption timing. Reframe to vertical with a shot-aware crop and safe zones. Captions use large words, with explicit RTL shaping and right-to-left layout for fa/ar; en/de use left-to-right layouts.

Platform duration limits are UNVERIFIED until each adapter is checked against current primary documentation. The same applies to loudness targets, formats, title limits, hashtags, and quotas. The planned auditor checks duration, aspect ratio, captions, spoiler scope, rights, loudness, and no repeated source segment. A short failure records an item result and leaves long-film state untouched.

Data model PROPOSED: `short_bundle(language, long_content_hash, status, approval_hash)`, `short_item(id, bundle_id, beat_ids, language, input_hash, output_hash, output_ref, status, excluded, rejection_reason, auditor_json)`, and `publish_attempt(id, item_id, platform, idempotency_key, status, error, attempt_count)`.

Long-film audio, captions, and still/video artifacts may be reused by hash after the long film is complete. Reuse is read-only and does not allow a short to mutate long-film approval.

## 4. Publishing design (PROPOSED)

Channel and language mapping comes from configured identity channels (`config/parameters/identity.json:2-32`) and channel intro lookup (`app/documentary/intros.py:86-100`). Each platform needs an OAuth flow scoped to the chosen channel/language. Tokens must be encrypted at rest with an AEAD algorithm such as AES-256-GCM or XChaCha20-Poly1305; the key must come from the macOS keychain or a deployment secret manager, never the repository, and rotation must retain a key id.

Publisher interface:

```python
class Publisher(Protocol):
    async def upload(self, item: PublishItem, account: Account, *, idempotency_key: str) -> UploadReceipt: ...
    async def poll(self, receipt: UploadReceipt) -> UploadState: ...
    async def publish(self, receipt: UploadReceipt, *, visibility: str) -> UploadReceipt: ...
    async def delete(self, receipt: UploadReceipt) -> None: ...
```

Adapters are PROPOSED for YouTube long/Shorts, Instagram Reels, Facebook Reels, and TikTok. Uploads use idempotency keys, resumable state, bounded retries, explicit quota errors, and private/draft mode unless the channel live switch is on. Every attempt writes an audit event. No live adapter exists today.

## 5. Approval and publishing state machine

States: `draft → in_review → approved → scheduled → uploading → published`; failure from upload goes to `failed`, human refusal goes to `rejected`, and an explicitly excluded item goes to `excluded`. Editing an approved item transitions it to `voided` and then `in_review`; other bundle items remain approved. Upload is permitted only when approval hash equals current content hash, item is not excluded/voided/rejected, and visibility policy allows it. A language without an account stays approved; release waits for “release now”.

| transition | trigger | invariant |
|---|---|---|
| draft→in_review | generated artifacts ready | auditor inputs exist |
| in_review→approved | one language approval click | bundle hash captured |
| in_review→rejected/excluded | reviewer decision | item reason recorded |
| approved→voided | item edit | only edited item changes |
| approved→scheduled | release schedule | hash still current |
| scheduled→uploading | worker claims attempt | idempotency key unique |
| uploading→published | platform receipt | visibility policy applied |
| uploading→failed | terminal platform error | retry reason/audit retained |

## 6. Per-platform requirements

All cells below are UNVERIFIED in this offline repository audit; adapter work must verify each item against primary platform documentation before implementation.

| platform | API product/app review | rate limits/quota | formats/length | auth/publish permissions |
|---|---|---|---|---|
| YouTube | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Instagram Reels | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Facebook Reels | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| TikTok | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |

## 7. UI design (PROPOSED)

The review queue shows one language bundle with long film plus shorts, per-item exclude/reject/edit actions, auditor reasons, and content hashes. The per-video overview shows all languages and bundle state. Bulk approve is one click per language; the repair-by-prompt panel previews one item’s invalidation; live mode is a per-channel switch; “release now” is explicit when no account is connected. Every screen handles draft, in review, approved, scheduled, uploading, published, failed, rejected, excluded, and voided. Keyboard focus, visible error text, captions, color-independent status, and RTL direction are required.

## 8. API endpoints (PROPOSED)

`GET /api/documentary/{case_id}/short-bundles`, `GET /api/shorts/{id}`, `POST /api/shorts/{id}/exclude`, `POST /api/shorts/{id}/reject`, `POST /api/shorts/{id}/approve`, `POST /api/shorts/{id}/edit-preview`, `POST /api/channels/{channel}/oauth/start`, `GET /api/channels/{channel}/oauth/callback`, `POST /api/documentary/{case_id}/release-now`, `POST /api/channels/{channel}/live-mode`.

Responses include state, content hash, auditor decision, account availability, visibility, and audit event id. Errors include `409` hash changed, `422` invalid review action, `503` account unavailable, `429` quota, and structured policy errors.

## 9. Implementation steps

1. Persist short bundles/items and hash-bound approval; test per-language approval and item isolation (medium risk).
2. Add post-render short scheduling and failure isolation; test long completion/release is unchanged by short failure (high risk).
3. Add vertical/caption renderer and per-short auditor with four-language fixtures (high risk).
4. Add encrypted account/token store and OAuth adapters one platform at a time (high risk).
5. Add resumable publishing, quota handling, private mode, live switch, and UI review queue (high risk).

## 10. Open risks and questions

Open risks: platform policies change; OAuth review can block production; RTL caption measurement can change line breaks; and a platform may accept an upload but delay publication. Questions: the exact key-management provider on the Mac, the first release platform, and allowed live-mode actors are unresolved.
