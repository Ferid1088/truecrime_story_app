# Phase 12: Short-Form UI Integration

## Implemented Surface

The `/short-form` workspace now contains these tabs:

```text
Candidates
Approved
YouTube Shorts
Instagram Reels
Facebook Reels
TikTok
Settings
Publishing Plan
Performance
Test Report
```

Candidate cards expose gate results and controls for approve, reject, edit, and
regenerate. Platform tabs show vertical safe-area previews, target duration,
caption/disclosure state, and the unavailable original-audio state. The
publishing-plan tab shows the 14-day review-only plan. Settings reads the live
short-form settings API and saves episode overrides through the existing API.

## Data Boundary

Real case-6 data used by the UI:

```text
case: 6
verification candidates: 5
source beats: B10, B15, B20, B30, B45
real reveal ids: C003, F021, F005, F011, F015
real visual asset ids: VIS_000001, VIS_000007, VIS_000013, VIS_000021, VIS_000031
persisted ShortFormConcept rows: 0
original media segments: 0
assets with original audio: 0
```

Because there are no persisted concepts, candidate actions are deliberately
local review state and are labeled `Verification data only`; they do not claim
to persist approvals. Performance is an empty state because nothing has been
published. Platform previews are UI previews, not exported videos. The
original-audio gap remains visible rather than being filled with synthetic
audio.

## Playwright Coverage

Command:

```text
npx playwright test e2e/phase12-shortform.spec.ts
```

Output:

```text
Running 3 tests using 1 worker
3 passed (4.2s)
```

Coverage includes the complete tab surface and data-boundary labels, candidate
approval/review-state interaction, publishing-plan visibility, and platform
preview safe areas plus the original-audio known gap.

## Build Verification

Commands:

```text
npm run lint
npm run build
```

Output:

```text
lint: 0 errors, 1 pre-existing warning in e2e/smoke-real.mjs
build: compiled successfully; TypeScript passed; 14/14 static pages generated
```

Implementation:

```text
frontend/app/short-form/page.tsx
frontend/e2e/phase12-shortform.spec.ts
```

Phase 12 UI coverage passes. Persistence of candidate approval and production
platform-fit selection remain intentionally unclaimed until approved concepts
exist in the database.
