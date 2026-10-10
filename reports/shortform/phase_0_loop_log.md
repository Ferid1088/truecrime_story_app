# Short-Form Engine Phase 0 Recon Loop Log

## Iteration 1

- Failure: None. Recon only; no code implementation attempted.
- Root cause: Not applicable.
- Fix: Mapped existing backend, config, database, tests, and frontend surfaces against the build spec.
- Result: Phase 0 gate passed once. Reuse map, gaps, and pilot choice are documented below.

## Iteration 2

- Failure: None. Second recon pass verified the same findings against the live DB, schema files, config, and UI test structure.
- Root cause: Not applicable.
- Fix: Confirmed `./truecrime.db` is the active app DB and that it currently has no persisted editorial blueprints, production scripts, or visual assets.
- Result: Phase 0 gate passed twice in a row. Stop here per build spec until user says "go".

## Interfaces To Reuse

### Long-form documentary artifacts

- `app.db.models.EditorialBlueprint`
  - Stores language-independent documentary beats in `blueprint_json`.
  - Existing docs/comments identify this as the source for beats, reveals, questions, intent, audio, and visual planning.
  - Existing helpers: `app.documentary.blueprint.latest_blueprint()` and `blueprint_dict()`.

- `app.db.models.ProductionScript`
  - Stores render-ready per-language long-form timeline in `script_json`, `audit_json`, and `render_json`.
  - Existing serializer: `app.documentary.api.production_dict()`.
  - Useful for deriving real timed narration, assets, subtitles, and rendered long-form context.

- `app.db.models.VisualAsset`
  - Existing case visual library with rights fields, source provenance, quality, entity metadata, `reveals_json`, original-audio flags, clip windows, local paths, thumbnails, and relevance tiers.
  - Existing serializer: `app.documentary.api.asset_dict()`.
  - Existing rights helper: `app.documentary.visuals.rights.allowed()`.

- `app.db.models.VisualPlan`
  - Existing language-independent visual plan tied to an editorial blueprint.
  - Useful for connecting short-form visual choices to real beat-level requirements.

- `app.db.models.VideoSource`, `TranscriptSegment`, `TranscriptClaim`, `ClaimCluster`, `Fact`, `Contradiction`, and `Source`
  - Existing evidence/provenance layer.
  - `TranscriptClaim.certainty`, `verification_status`, `supporting_source_ids_json`, and promoted facts can seed epistemic modality mapping for short-form claims.

- Existing render stack
  - `app.documentary.render.engine`
  - `app.documentary.production.script`
  - `app.documentary.voice_render`
  - `app.documentary.audio`
  - These should be reused before introducing a short-form renderer.

### Central config to reuse

- `config/ai_config.json`
  - Central channel config already exists under `channels`.
  - Central voice config already exists under `voice.languages`.
  - Required reference voice IDs already match the spec:
    - EN `UF84IGrTBtegPkgbbrS2`
    - DE `02KhC7wycOLwuF6sc5Qu`
    - FA `I3gMKh0nwZ8NQXKqUg6F`
    - AR `EFlRMcr2Nd9ah6iW85Z4`

- `app.core.ai_config`
  - Validated typed config loader.
  - New `short_form` config should be added here and in `config/ai_config.json`, not hard-coded elsewhere.
  - New short-form LLM roles should be routed through this loader if/when generation roles are needed.

- `app.documentary.studio.channel_profile(language)`
  - Existing central lookup combines channel name, studio profile, voice ID, and avatar configuration.
  - This is the right lookup point for channel/voice consistency.

### API/UI patterns to reuse

- FastAPI routers use `/api/...` paths and Pydantic request models, with direct SQLAlchemy sessions from `app.db.base.get_db`.
- The documentary API is mounted from `app.documentary.api` and already exposes settings, assets, plans, scripts, renders, and files.
- Frontend is Next.js App Router with shared API helpers in `frontend/lib/api.ts`, shared types in `frontend/lib/types.ts`, and reusable controls in `frontend/components/ui`.
- Existing Playwright tests live in `frontend/e2e/app.spec.ts` and use route-level mocked API fixtures in `frontend/e2e/fixtures`.

## Gaps To Implement In Later Phases

- No short-form database models exist yet:
  - `ShortFormConcept`
  - `ShortFormScript`
  - `PlatformVariant`
  - `CrossPlatformFunnel`
  - `ShortFormPublishingPlan`
  - `ShortFormMetric`

- No `short_form` config section exists yet in `config/ai_config.json` or `app.core.ai_config`.

- No deterministic short-form hard gate module exists yet:
  - Reveal firewall for text, audio, and visual frames.
  - Epistemic checker against source modality.
  - Rights checker using the stricter short-form rights statuses/risk model.
  - Policy risk checker.
  - Disclosure checker.
  - Duration checker.

- Reveal support is partial:
  - `VisualAsset.reveals_json` exists.
  - `EditorialBlueprint.blueprint_json` is intended to contain reveals.
  - There is no first-class `RevealGraph` model/table yet.

- Epistemic support is partial:
  - Facts and transcript claims have confidence/certainty/verification fields.
  - There is no first-class `EpistemicContracts` model/table yet.
  - Phase 1 should create deterministic modality mapping and tests before any director/ranking work.

- Rights support is partial:
  - `VisualAsset.rights_status` exists, but values differ from the spec. Current code mentions values such as `creative_commons`, `editorial_review_required`, `permission_required`, and `do_not_use`.
  - The spec's short-form statuses and platform claim risk need a compatibility layer, not silent replacement.

- Original media support exists at the schema level (`original_media_segments` table in the live DB), but no Phase 0 code path was found yet that exposes it as a short-form source interface.

- No short-form UI exists yet:
  - No `/short-form` route.
  - No Settings panel.
  - No distribution overrides or publishing-plan UI.

- No `scripts/shortform_verify.sh` exists yet.

- Current local `truecrime.db` has cases and story versions but no persisted editorial blueprints, visual assets, or production scripts. Later phases need either a seeded fixture pilot or a completed long-form run before candidate generation can truthfully use real beats/assets.

## Chosen Pilot Episode

Chosen pilot: case `2`, `The Nannup Four`, language `en`, status `story_ready`.

Reasoning:

- It is one of the strongest local candidates because it has multiple story versions and an existing exported pilot artifact in `Claude outputs/nannup_four_*`.
- It is in English, matching the first end-to-end short in Phase 4.
- The project already contains multiple Nannup-related output artifacts and blueprints outside the live DB, so it is the best candidate for seeding/reconstructing completed long-form context if needed.

Fallback pilot:

- case `1`, `The Lighthouse Keeper Vanishing`, language `en`, status `researched`.
- This is useful for tests and UI fixtures because existing Playwright fixtures already use a lighthouse case, but the live DB currently lacks the long-form assets required by the short-form mission.

## Phase 0 Gate Status

Passed twice in a row.

No implementation should start until this report is accepted and the user says "go".
