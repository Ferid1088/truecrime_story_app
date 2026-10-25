# Main Visual Planner — Read-only Repository Audit and Migration Plan

**Repository:** `Ferid1088/truecrime_story_app`
**Audit branch:** `audit/visual-planner-architecture-v2` (created from `main`)
**Scope:** Approved Steps 1–12, especially new visual research, shared media library, verification, selection, direction, film integration, budget and multilingual gates.
**Status:** Source-level architecture audit and migration blueprint; NOT runtime certification. No application code, database migration or budget configuration has been changed.

## Executive summary

The repository already has a substantial documentary pipeline; a replacement must be selective. Protect factual research, story writing, Host, and music. Replace *visual AI intelligence and prompts* from scratch while retaining suitable tested deterministic infrastructure. Four material mismatches are visible:

1. `app/documentary/jobs.py` checks final **story text** approval per language and organizes language work in parallel. It lacks the required owner approval of the **completed English film** before DE/FA/AR audiovisual production.
2. `app/documentary/visuals/coverage.py` calculates per-minute/per-beat picture category time shares, not complete semantic fulfillment of visual requirements; `visual_direction.min_beat_coverage` is 0.5. This cannot certify mandatory 100% meaningful narration coverage.
3. `config/parameters/visuals.json` has `rights.enforce: false`. `app/documentary/jobs.py` explicitly skips visual rights checking when enforcement is off. Its publish-profile allowed statuses are narrower than preview, but the current global disabled flag is a critical configuration finding. Publication hard gates need reliable enforcement, not just a render profile.
4. `app/services/budget.py` checks *already recorded* per-case spend against `Case.total_cost_cap_eur` / configured default; it expressly does not reserve estimates before concurrent calls. The €300 scope must be reconciled with the approved application-wide budget intent before changing values. Historic ledger must remain.

Additional: `app/documentary/visuals/gaps.py` plans search per entity/weak sentence and `config/parameters/visuals.json` has broad query/download caps; the approved one-initial-plus-two-further *per unmet requirement* external search rounds require a new persistent requirement-based budget. `app/documentary/visuals/director.py` scores `needs_review` assets and uses sentence-level direction and fixed shot timing heuristics; introduce separate mandatory eligibility and semantic review. `app/shortform/pipeline.py` enqueues Shorts after any one completed documentary job, instead of after all four full films. Existing `app/documentary/production/host_timeline.py` already shifts shots/music/voice/subtitles and splices Host audio: preserve it and establish a single authoritative once-only integration boundary.

## Source observations and proposed classification

| File / area | Observed behavior | Proposed class | Migration |
| --- | --- | --- | --- |
| `app/documentary/jobs.py` | Pipeline orchestration, final story approval, visual stages and parallel language progression | MODIFY | Add completed-English-film approval gate, reorder EN-first then DE/FA/AR, avoid duplicate orchestration |
| `app/documentary/pipeline/base.py` | StageRunner checkpoints, stored results, resume/skips | KEEP / MODIFY | Reuse durable stages; add new visual pipeline and explicit readiness contract |
| `app/core/artifact_dependencies.py` | Declarative shared/per-language artifact DAG, described as shadow-only | MODIFY | Promote tested selective invalidation; add provenance and approved film gates; avoid premature enforcement |
| `app/agents/visual_planner.py` | Old model invocation `visuals.plan` | REPLACE | Fresh semantic narrative requirement agent and prompt |
| `app/documentary/visuals/planner.py` | Old planner domain rules, evidence matching and alias utilities | MERGE / REPLACE | Retain tested pure helpers if safe; replace visual AI prompt and semantic requirement schema |
| `app/agents/visual_director.py` | Old `visuals.direct` agent, per-beat/sentence direction | REPLACE | New separated selector/director/reviewer contracts and prompts |
| `app/documentary/visuals/director.py` | Ranked candidates, deterministic filters and direction helpers | MODIFY / MERGE | Reuse safe algorithms selectively; hard-gated candidate eligibility, versioned visual direction, no unrestricted `needs_review` admission |
| `app/documentary/visuals/research.py` | Source-page and Wikimedia discovery, case-tied VisualAsset writes | MODIFY | Preserve provider adapters; library-first discovery, round ledger per unmet requirement |
| `app/documentary/visuals/footage.py` | Wikimedia / Internet Archive footage, muted archival clip derivatives | MODIFY | Preserve safe technical download/transcode; source provenance and rights verified independently |
| `app/documentary/visuals/verification.py` | Existing vision model with `verified / needs_review / rejected` statuses and confidence rules | REPLACE visual intelligence; KEEP compatible helpers | New independent multi-gate provenance/factual/rights/scene/film verification |
| `app/documentary/visuals/rights.py` | Provider-based rights classification and render-profile handling | MODIFY | Legal and publication-use checks independent of provider heuristics |
| `config/parameters/visuals.json` | `rights.enforce=false`; fixed cut pattern and default duration; provider limits | MODIFY | Mandatory rights gates; advisory duration only; approved search-round semantics |
| `app/documentary/visuals/gaps.py` | Weak sentence / entity search and subsequent redirection | REPLACE orchestration; MERGE utilities | Requirement-scoped shortage handling, bounded autonomous alternatives and one consolidated report |
| `app/documentary/visuals/usage.py` | MediaUsage and category-specific repeat limits | MODIFY | Context-sensitive repetition evaluation; keep usage audit, remove mechanical hard media caps where harmful |
| `app/documentary/visuals/coverage.py` | Picture category share per beat and minute | MODIFY / MERGE | 100% temporal interval + semantic requirement coverage, distinct quality gates |
| `app/documentary/detective_board.py` | Language-neutral deterministic cards selected from narrated blueprint facts | MODIFY | Preserve accurate card logic; backed by typed KG claims and reveal-state verification |
| `app/agents/detective_board.py` | Existing AI title writer for native-language cards | REPLACE visual AI prompt | New localization/graphic text generator with approved factual source contract |
| `app/documentary/production/script.py` | Language-specific production script with timing, crops and media usage | MODIFY | Consume approved visual direction and canonical timing/rights metadata |
| `app/documentary/production/host_timeline.py` | Host insertion shifts shots, subtitles, voice and music and splices audio | KEEP — protected | Adapter and regression tests; apply once; do not rewrite Host logic |
| `app/documentary/host.py` | Existing Host director/writer/critic and persona | KEEP — protected | Read-only integration |
| `app/documentary/audio_director.py` | Existing music/silence Audio Director | KEEP — protected | Preserve prompts, selection and timing |
| `app/documentary/music.py` | Existing music library, track reuse and mixer | KEEP — protected | Consume only its authoritative outputs |
| `app/services/budget.py` | Case cap and provider-call ledger; pre-call realized spend check | MODIFY | Verify application-vs-case scope; €300 cap reset, atomic reservations, unknown cost reconciliation |
| `app/db/models.py` | Existing SQLAlchemy models for Case, VisualAsset, VisualPlan, Artifact, ArtifactDep, ProviderCall and Shorts | MODIFY | Add compatible KG/provenance/verification/film-approval relations only after schema diff |
| `app/shortform/pipeline.py` | `enqueue_for_finished_job` triggers for one completed job | MODIFY | Enforce all-four-full-films condition before Shorts admission |
| `frontend/` | Next.js 16, React 19, Playwright configured | AUDIT / MODIFY | Inspect actual components, exposed controls, RTL, approved-film playback and versioned edits |

**Classification is provisional for components not fully traversed.** No definitive REMOVE decisions until runtime call sites, schema migrations and tests are traced.

## Target contracts

1. **Protected boundary:** factual research, story writer, Host and Music are authoritative; visual services have read-only access to their outputs and cannot mutate protected prompts/plans.
2. **Graph:** PostgreSQL/SQLAlchemy/Alembic typed case-specific entities, sourced claims and uncertain relationships; no Neo4j/LangGraph dependency.
3. **Media:** immutable canonical byte-hash originals; source editions and rights kept separate; perceptual hashes suggest relationships, not auto-merges; reversible dedupe, DAG derivatives and reference-aware cleanup.
4. **Research:** stored factual YouTube research, video downloads and transcripts reused; creator footage only leads unless rights independently clear. Exact one initial plus at most two materially distinct external discovery rounds PER unmet visual requirement, persisted across retries.
5. **Verification:** source authenticity, identity/context, transformation integrity, usage rights, ethics, exact scene suitability, final render and publication readiness remain separately evidenced. Unknown/review never implies pass.
6. **Visual planning:** English narration fully covered at semantic beat and narration interval level; rank only eligible candidates; film-wide pacing/repetition; no fabricated historical evidence; deterministic factual graphics.
7. **Direction:** new visual AI prompts from scratch; validated declarative render instructions; keep exact media/source ancestry.
8. **Timeline:** one canonical compiled manifest with explicit narration/source/final time domains; apply Host insertion and music conversion exactly once; frame-accurate tests and immutable approved versions.
9. **Gates:** complete EN film, owner WATCHES and explicitly approves exact rendered checksum, THEN DE/FA/AR in parallel; Shorts only after all four full films complete.
10. **Cost:** configure agreed €300 limit at confirmed application scope; preserve historical spend, distinguish actual, estimated, unknown and reserved; atomic admission control.
11. **Owner UI:** access to all stage decisions, sources, alternatives, preview/corrections and costs without mandatory per-scene approvals.
12. **Release:** independent audit, regression and full-film output verification; no silent changes to owner-approved films.

## Ordered implementation work packages (NO CODE CHANGES AUTHORIZED BY THIS DOCUMENT)

| WP | Scope | Prerequisite | Acceptance |
| --- | --- | --- | --- |
| WP0 | Baseline lock: record branch commit, migrations, configuration, tests and protected behavior | None | Reproducible read-only baseline; local uncommitted changes separately checked |
| WP1 | Complete source map: API routes, agents, prompts, all migrations, frontend, shorts, existing YouTube assets | WP0 | Fully evidenced KEEP/MODIFY/MERGE/REPLACE/REMOVE matrix |
| WP2 | Budget audit: global vs case scope; €300 setting, ledger and reservations | WP1 | Historical spend intact, concurrency tests, explicit unknown cost |
| WP3 | Compatible graph/media provenance migration and reversible backfill | WP1 | Alembic upgrade/downgrade fixture; immutable originals; no mistaken automatic merge |
| WP4 | New media research, round ledger, independent verification prompts and service contracts | WP3 | Source evidence and rights, exhaustive quota tests, prompt-injection defense |
| WP5 | New semantic requirements + selection + visual direction from scratch | WP4 | 100% narration coverage and film-wide evaluation; no fake evidence |
| WP6 | Fact-backed graphics and per-language render derivatives | WP3, WP5 | Map/board/timeline/document truth, RTL and reveal tests |
| WP7 | Canonical production manifest, existing Host/music adapters, English completed-film approval and Shorts lock | WP5, WP6 | No double Host, no audio shift, backend EN-first enforcement |
| WP8 | Owner UI and telemetry | WP2, WP4-WP7 | All capabilities inspectable/editable with versioned selective revalidation |
| WP9 | End-to-end + adversarial + stress + cost certification | WP0-WP8 | Long-film render, protected regression, recovery, provenance and gates |
| WP10 | Controlled cutover and legacy visual AI retirement | WP9 | Old visual prompts unreachable, migration rollback tested, main untouched until review |

## Required regression/acceptance matrix

- Research/story: identical authoritative source/final story behavior and stored YouTube research reuse.
- Host: existing planning, studio/outfit, camera/pose, video and narration-offset behavior unchanged; no duplicate insertion.
- Music: existing audio prompts, track reuse, silence, placement and mix unchanged; no duplicate shifts.
- Visual: exact and contextual media correctly distinguished, no unsafe `needs_review` promotion, truthful rights gate, factual graph-backed boards and routes.
- Coverage: long narration fully temporally and semantically covered; generic imagery never earns unsupported semantic pass.
- English-first: unapproved EN render prohibits any DE/FA/AR audiovisual job; changed checksum invalidates approval as required.
- Shorts: a single completed film does not enqueue eligible Shorts; all four full films do.
- Budget: concurrent calls cannot reserve the same remaining funds; unknown provider costs never recorded as zero.
- Reliability: retries do not rerun completed valid jobs; incomplete output never marked published; output checksum pinning.
- Multilingual: native EN, DE, FA, AR synchronization and RTL text correct.
- YouTube: accurate origin classifications and disclosure recommendations, no false monetization or rights guarantee.

## Findings still requiring verification

- Full frontend route/component inventory and available editing controls.
- All active visual prompt paths, staged fallbacks and provider registrations.
- Exact current PostgreSQL migration head and migration ordering.
- Detailed VisualAsset/rights and provider-use contracts.
- Full historical factual YouTube ingestion and media-storage reuse.
- Current actual budget default and local database case caps (not available through GitHub alone).
- Local working tree state, secrets, storage and existing data (GitHub cannot establish this).
- Actual rendered audiovisual quality and test pass rate (no runtime execution performed).
- Actual owner playback and approval UI semantics.
- Current test coverage and CI results against the new branch.

## No-change declaration

This report is documentation only, on the dedicated audit branch. No application source, prompts, data, budget configuration, migration, tests or protected subsystem has been modified. The migration is proposed, not executed.
