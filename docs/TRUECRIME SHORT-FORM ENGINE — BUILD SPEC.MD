# TRUECRIME SHORT-FORM ENGINE — BUILD SPEC

Project: `/Users/feri/truecrime_story_app`

---

## A. HOW TO WORK

1. **Phases, in order.** Never start a phase before the previous one passed its gate. After each phase: STOP, report, wait for my "go".
2. **Every phase runs two loops.**
   - **Loop A (this part):** build backend + UI + tests together → run tests → inspect the real artifact (watch the video, screenshot the UI, read the generated text) → fix → repeat. Exit when the phase gate passes **twice in a row**.
   - **Loop B (everything so far):** run `scripts/shortform_verify.sh --through <n>` = all tests from Phase 0 to now + the long-form pipeline tests + a full pilot-episode run. Fix regressions, repeat until green **twice in a row**, then re-run Loop A's gate once.
3. **Loop limits.** Max 8 iterations per loop. At the limit: STOP, report root cause, attempts, and the decision you need. Never loop forever, never give up quietly. Log each iteration to `reports/shortform/phase_<n>_loop_log.md` (iteration, failure, root cause, fix, result).
4. **Never fake green.** No deleting or skipping tests, loosening thresholds, mocking away the thing under test, or hard-coding expected output. If a test is itself wrong, say so and show the exact diff.
5. **Reuse, don't duplicate.** Before writing code, read: RevealGraph, EpistemicContracts, EditorialBlueprint, ProductionScript, EpisodeIdentity, VisualAsset library, OriginalMedia library, central channel config, central voice config, existing renderer. Channel names, voice IDs and credentials live in central config only.
6. **Never break long-form.** Its tests run in every Loop B.
7. **UI ships with each phase**, not at the end: every capability gets its UI + a Playwright test in the same phase.
8. **Report honestly.** Failed gates, weak output, missing modules and gaps are stated plainly. If something required does not exist, say so — never invent it silently.

---

## B. MISSION

Turn each finished long-form documentary into a small set of strong, distinct vertical clips for **YouTube Shorts** (`youtube_short`), **Instagram Reels** (`instagram_reel`), **Facebook Reels** (`facebook_reel`), **TikTok** (`tiktok_video`), in **EN / DE / FA / AR**, each driving viewers to the long-form documentary.

Channels (central config only): EN ClueVera · DE Fallspur · FA رد خاموش · AR أثر خفي

**Core principle:** generate many candidates, publish few. Quality > quantity. A target count is never a reason to approve a weak clip.

**Pipeline:**
```
documentary → Director (candidates from real beats+assets)
→ HARD GATES (deterministic pass/fail)
→ soft ranking + diversity selection
→ human approves CONCEPTS
→ derived platform variants (EN/DE/FA/AR)
→ 9:16 render → critic → human review
→ export package  (later: SocialPublisher APIs)
→ metrics → learning (only above data threshold)
```

---

## C. THE FOUR DESIGN DECISIONS THAT MATTER

### C1. Hard gates are code, not opinion
Binary pass/fail, implemented deterministically. A failure is never averaged away by a good score.

| Gate | Rule |
|---|---|
| **RevealFirewall** | Each episode has a `spoiler_horizon` (allowed RevealGraph nodes). A clip may only use beats, claims, text, audio **and visual frames** inside it — a face, location or document spoils on its own. Reject anything exposing culprit identity, late-stage evidence, hidden relationship, unexpected location, key contradiction or ending. Store `allowed_reveals`, `forbidden_reveals`, `spoiler_check_result` (+ offending node ids). |
| **EpistemicChecker** | Every short claim carries its source claim's modality from EpistemicContracts (`ESTABLISHED`, `BELIEVED_BY_INVESTIGATORS`, `ALLEGED`, `ABSENCE_OF_EVIDENCE`, `DISPUTED`). FAIL if the short is stronger than the source. "investigators believed X" must not become "he did X"; "no evidence was found" must not become "it never happened". Hedges survive compression. |
| **RightsChecker** | Every asset has `rights_status` (`owned` / `licensed` / `public_domain` / `fair_use_review` / `unknown`) and `platform_claim_risk`. `unknown` or high-risk original audio/footage requires explicit human sign-off and a per-platform flag (Instagram and TikTok are stricter). |
| **PolicyRiskChecker** | Flags real victims, minors, graphic violence, identifiable private individuals, ongoing cases. Outputs `policy_risk` per platform with reasons; high risk requires human review. |
| **DisclosureChecker** | Synthetic voice or realistic generated visuals must set the required AI-disclosure metadata for YouTube, TikTok and Meta. Missing disclosure blocks export. |
| **DurationChecker** | Clip fits its platform's min–max; hard max 60 s. |

### C2. Soft scores only rank survivors
Only candidates that passed every hard gate get scored: **hook strength, case specificity, brand fit, repetition score** (computed from embeddings + text overlap), plus **automation-feel** as a pass/fail flag. Keep the set small; calibrate against my real approve/reject decisions (Phase 5).

### C3. Candidates come from real material
The Director selects moments that exist in the documentary and already have strong assets or original audio. It does not invent angles or scenes. Visual priority: real person image → real evidence → real document → real location → real original video → real contextual footage.

### C4. Diversity by selection, approval by concept
- **Selection:** max-marginal-relevance — take the best candidate, then repeatedly take the best `score − similarity(already selected)`. Similarity spans hook wording, concept type, source beat, evidence, opening visual, question. A set that is all one concept type fails the diversity gate.
- **Approval:** I approve **concepts**. Platform variants are derived from them. Render the base video **once per language**; vary only cheap parameters per platform (hook wording, caption, CTA/end card, cover frame, safe area, clean duration trim). I spot-check only what differs.

---

## D. CONTENT RULES

**Concept types:** MYSTERY_HOOK · CONTRADICTION · REAL_EVIDENCE · DOCUMENT_MOMENT · HUMAN_MOMENT · LOCATION_OR_MAP · ORIGINAL_AUDIO · UNEXPECTED_DETAIL · HOST_DIRECT_TO_CAMERA · QUESTION_DRIVEN · TIMELINE_MOMENT · INVESTIGATIVE_TURN · FULL_STORY_TEASER

**Hooks.** Strong first 1–3 s: surprising fact, contradiction, visual, short question, host line, or original audio. Banned: "You won't believe…", "This case is crazy…", "What happened next shocked everyone…", "The truth will blow your mind…". No two clips of an episode share a hook structure.

**CTA.** Natural, premium, native per language. Good: "The full story is on Fallspur." / "The complete documentary is available on ClueVera." / "The rest of the case is in the full documentary." Banned: LIKE NOW, SUBSCRIBE NOW, LINK IN BIO!!!, YOU HAVE TO WATCH THIS. Vary CTA sentences within an episode.

**Destinations.** YouTube Short → the related full documentary / channel, no off-platform CTA. Instagram & TikTok → "Full documentary on YouTube — <channel>" + profile-link destination metadata. Facebook → softer CTA, page/channel destination. A URL printed in the video is never the only funnel mechanism.

**Languages.** EN/DE/FA/AR are **written natively** from the same concept, never translated: adapt hook, cadence, sentence length, CTA, subtitle rhythm. Voices come from central ElevenLabs config only (reference values for the config test: DE `02KhC7wycOLwuF6sc5Qu`, AR `EFlRMcr2Nd9ah6iW85Z4`, EN `UF84IGrTBtegPkgbbrS2`, FA `I3gMKh0nwZ8NQXKqUg6F`). FA/AR need correct shaping, bidi, fonts, line breaking and safe areas — tested in Phase 4, not later.

**Host (Fereidoun).** Recognizable signature, not in every clip. Patterns: HOST → CASE MATERIAL · CASE MATERIAL → HOST CTA · ORIGINAL AUDIO → HOST · FULL ASSET-LED CLIP. Watch for a repeated host pose.

**Craft.** 9:16, 1080×1920. `VerticalReframeService` does face-, entity- and document-aware cropping: never cut faces or evidence, never make a document unreadable. Subtitles from audio alignment: few words per line, large, safe-zone aware, never covering faces or evidence, no karaoke animation. On-screen text sparingly (date, location, short quote, key time, one central fact). Original audio: narrator sets up → **pauses** → original clip → narrator continues or closes; the narrator never talks over important original speech. Sound follows long-form rules: no constant impacts, whooshes, cheap stingers or fake crime sounds. Cover frame: clean vertical — Fereidoun + one authentic case visual + a short title fragment; never the crowded long-form thumbnail.

**Automation-feel.** Reject formulaic, over-edited, repetitive, template-like or clickbait output. Check for repeated hook structure, CTA, visual rhythm, silence pattern, host pose, music cue — across the episode and against recent clips on the same channel.

---

## E. DATA MODEL & CONFIG

**Models**
- `ShortFormConcept` — id, case_id, episode_identity_id, concept_type, source_beat_ids, evidence_ids, asset_ids, allowed_reveals, forbidden_reveals, claims[{source_claim_id, modality}], gate_results, soft_scores, status
- `ShortFormScript` — + language, hook, body, cta, target_duration, visual_requirements, original_audio_opportunity
- `PlatformVariant` — concept_id, platform, language, hook, caption, description, cta, end_card, subtitle_layout, cover_frame, duration, destination, disclosure_flags, rights_flags, policy_flags, status
- `CrossPlatformFunnel` — platform, source_episode_id, destination_type (`youtube_episode`|`youtube_channel`|`profile_link`), destination_url, campaign_id (e.g. `case_023_tiktok_03`), utm_source, utm_campaign, attribution_status
- `ShortFormPublishingPlan`
- `ShortFormMetric` — views, unique_viewers, average_watch_time, completion_rate, rewatches, likes, comments, shares, saves, profile_visits, link_clicks, youtube_clicks, new_subscribers, destination_clicks, youtube_episode_visits, estimated_conversion_rate, attribution_status

**Statuses:** draft → generated → quality_checked → needs_review → approved → scheduled → publishing → published / failed

**Config `short_form` — the single source of truth for all numbers:**
```json
{
  "candidate_multiplier": 1.75,
  "publish_target": 8,
  "distribution": {
    "youtube_short":  { "enabled": true, "count": 5, "min_seconds": 20, "target_seconds": 35, "max_seconds": 60 },
    "instagram_reel": { "enabled": true, "count": 6, "min_seconds": 20, "target_seconds": 30, "max_seconds": 45 },
    "facebook_reel":  { "enabled": true, "count": 5, "min_seconds": 25, "target_seconds": 35, "max_seconds": 60 },
    "tiktok_video":   { "enabled": true, "count": 8, "min_seconds": 15, "target_seconds": 28, "max_seconds": 45 }
  },
  "hard_max_seconds": 60,
  "dynamic_count": { "weak": [4, 5], "normal": [6, 8], "strong": [9, 12] },
  "release_window_days": [10, 14],
  "learning_min_published_shorts": 30,
  "learning_min_episodes": 5
}
```
Candidates generated = `round(max(enabled platform counts) × candidate_multiplier)` → 14 by default. Nothing above may be hard-coded elsewhere.

**Dynamic count.** Weak episode 4–5 clips, normal 6–8, strong 9–12 — driven by hook diversity, visual quality, story diversity, spoiler safety, original-audio availability, case strength, repetition risk. Fewer than target is a valid outcome.

**Attribution honesty.** UTM-tagged links where allowed; YouTube Analytics traffic sources for Shorts → long-form. Elsewhere, if attribution is unavailable, store `attribution_status = "unavailable"` and use proxy metrics. Never invent or estimate conversion. The learning loop reports conclusions only above the data thresholds; below them it collects and displays only. Optimize for qualified viewers, not views.

---

## F. DISTRIBUTION SETTINGS UI (I set counts and lengths)

Panel in **Short-Form → Settings**, also shown as step 1 before generating for an episode. Every field pre-filled from `short_form.distribution`, so I can accept defaults with one click.

| Platform | Count | Length (min / target / max) |
|---|---|---|
| YouTube Short | 5 | 20 / 35 / 60 s |
| Instagram Reel | 6 | 20 / 30 / 45 s |
| Facebook Reel | 5 | 25 / 35 / 60 s |
| TikTok Video | 8 | 15 / 28 / 45 s |

**Layout.** One row per platform: enabled toggle · count (stepper + typed input) · approximate length (target + min–max range slider) · per-row "Reset to default". Plus: language selector (default = episode's channel language, option: all four) · live summary (total videos, total runtime, candidates that will be generated) · "Reset all" · Save with unsaved-changes indicator · capacity hint ("this episode has ~N distinct usable beats", computed from real beats and assets) warning me when my counts exceed it.

**Behavior.**
- Three default levels: global → channel/language → episode override. The UI shows which level each value comes from.
- **Counts are targets, not quotas.** If fewer clips pass the gates: show "Requested 8, qualified 6" with rejection reasons and offer *regenerate with more candidates* or *accept fewer*. There is **no** option to relax a hard gate or force a weak clip.
- **Lengths are approximate.** Clips are fitted inside min–max; actual duration is shown per clip; above `hard_max_seconds` is blocked.
- Validation: count 0–20, `min ≤ target ≤ max`, `max ≤ 60`, inline errors.
- Changing counts after approval re-plans distribution and schedule only; approved concepts are never discarded.
- Settings are stored with the episode and written into the publishing plan.
- Keyboard accessible, responsive, correctly mirrored for FA/AR.

---

## G. PHASES

Each phase = Loop A → Loop B → stop and report. The listed gate is the minimum.

| # | Phase | Gate (must pass twice) |
|---|---|---|
| **0** | **Recon** (no code) | Written map of the interfaces to reuse, the gaps, and the chosen pilot episode with reasoning. |
| **1** | **Models + hard gates** (section C1) + config + central channel/voice lookup | Unit tests: forbidden reveal rejected (text, audio **and** visual frame); stronger-than-source claim rejected; unknown rights flagged; missing disclosure blocks export; duration enforced. Long-form tests pass. |
| **2** | **ShortFormDirectorAgent** on the pilot | 14 candidates (count from config), each tied to real beat ids, evidence ids and assets. I can read all 14 in a table: type, hook, beats, assets, forbidden reveals. Manual inspection. |
| **3** | **Gates + ranking + MMR selection** + dynamic count | Unsafe/weak candidates rejected with reasons; selected set diverse (not one type, no duplicated hook structure); fewer than target allowed and explained; rejection log readable. |
| **3A** | **Distribution settings + UI** (section F) | UI defaults equal config exactly; changing a count or length demonstrably changes selection, clip fitting and plan; shortfall banner with reasons and no force path; validation, resets, unsaved indicator, capacity warning. Playwright: edit, reset, invalid input, persistence, override precedence, RTL. |
| **4** | **One EN YouTube Short end to end** — script, voice, reframe, subtitles, CTA, cover, export package with disclosure metadata, minimal review page (preview / approve / reject / edit caption / change CTA / change destination). **Plus FA + AR subtitle render test now.** | A watchable 1080×1920 Short, correct subtitles, no safe-area violation, disclosure metadata present, RTL shaping/bidi/fonts verified in the rendered video. I review it. |
| **5** | **Critic calibration** | Critic output matches my Phase-4 decisions, or each disagreement is explained and tuned. Calibration data stored. |
| **6** | **Four native languages** | Native quality (not translation) confirmed by me; voice IDs resolved from central config; RTL correct in final video. |
| **7** | **Derived platform variants as export packages** | Variants differ only in the intended parameters; CTA and destination correct per platform; funnel records with campaign ids; rights/policy/disclosure flags per platform; base video rendered once per language. |
| **8** | **Original audio + rights** | Narrator never overlaps important original speech; an `unknown`-rights asset forces human sign-off and a platform warning. |
| **9** | **Adversarial reveal + epistemic tests** | Late-reveal material (text, audio, visual) and stronger-than-source claims are rejected **by the deterministic gates**, not the critic. |
| **10** | **Repetition + automation feel** | No repeated hook structure, CTA sentence, opening visual or host pose beyond threshold, within the episode and against recent channel clips; a deliberately repetitive set is rejected. |
| **11** | **Publishing plan** | Configurable 10–14 day plan (e.g. D1 Short · D2 Reel/TikTok · D4 Short · D6 Reel/TikTok · D8 Short · D10 Reel · D12 TikTok · D14 reminder); respects platform counts; no bulk publishing; nothing scheduled without approval. |
| **12** | **UI integration + polish** — tabs: Candidates · Approved · YouTube Shorts · Instagram Reels · Facebook Reels · TikTok · Settings · Publishing Plan · Performance · Test Report | Candidate cards show preview, type, hook, duration, language, source beat, spoiler status, gate results, scores, platform suitability, with approve/reject/edit/regenerate. Platform preview shows 9:16 frame, safe areas, CTA, cover, caption, destination. Every short supports preview, approve, reject, edit caption, change CTA, change destination, change publish time. No dead buttons; sensible empty/loading/error states. Playwright green. |
| **13** | **Metrics + funnel (mocked)** | Metrics persist per platform; concept types comparable; raw views never compared across platforms without context; unavailable attribution stored as such; no invented conversion; analyzer silent below thresholds. |
| **14** | **Publisher abstraction** | `SocialPublisher` interface + working `ExportPackagePublisher`; YouTube/Instagram/Facebook/TikTok publishers are stubs; tests prove approval is required before `scheduled`/`publishing` and that generation code holds no credentials. |
| **15** | **Final verification loop** | See below. |

### Phase 15 — final verification
1. Loop the full suite — pytest, tsc, lint, build, Playwright, long-form regression, `scripts/shortform_verify.sh --all` — until **3 consecutive fully green runs**.
2. Run end to end on the pilot **and a second episode** (different case, ideally different channel/language) to catch overfitting.
3. Add tests until "enough" is demonstrable: every hard-gate rule has positive + negative + edge tests; **≥95 % line coverage on gate modules** (report the real number); adversarial suite (late reveals, stronger claims, unknown rights, banned hooks/CTAs); determinism (same input → same gate result); idempotency (re-running creates no duplicate concepts, variants or schedule entries); failure paths (missing asset, TTS failure, render failure, bad settings, interrupted run); all settings-UI paths; performance smoke (render time per short recorded).
4. **Show the results:** write `reports/shortform/final_test_report.json` + `.html`, and populate the UI **Test Report** tab with, per phase: tests run, passed/failed, Loop A/B iterations needed, coverage, last run time, links to loop logs. Same summary in the final chat report.

**Gate:** 3 consecutive green runs · second episode passes · coverage and test-count table delivered · every failure or skipped test listed with its reason.

---

## H. LIVE PILOT (after Phase 15)

One finished episode → 14 candidates → best 6–8 → export packages for 3 YouTube Shorts, 3 Instagram Reels, 3 TikTok, 2 Facebook Reels. **Do not publish.**

Review each clip against: (1) strong first 2 s? (2) case-specific? (3) understandable alone? (4) creates curiosity? (5) no main-reveal spoiler? (6) factual nuance preserved? (7) visually premium? (8) same channel host? (9) native in this language? (10) natural CTA? (11) would I click through? (12) does it feel AI-generated — if yes, name the exact reason and repair.

---

## I. FINAL REPORT

Episode (case, channel, title) · Candidates (generated / approved / rejected + reasons) · Approved concepts (type, hook, duration, source beat, spoiler status, gate results, scores) · Platform distribution · Settings used (defaults vs my changes; requested vs qualified per platform + shortfall reasons) · Languages incl. RTL result · Funnel (destination, CTA, campaign ids, attribution status) · Risk flags (rights, policy, disclosure) per clip · Schedule · Quality (hook, factual, spoiler, visual, native, brand fit, automation feel) · Loops (iterations per phase and what failed) · Tests (counts per category, coverage, the 3 green runs, second-episode result, anything skipped and why) · **Verdict: A** production-ready / **B** works, gaps remain / **C** still repetitive, unsafe or automated.

---

## J. HARD RULES

1. Generate many, publish few — never force a count, never approve a weak clip.
2. Hard gates are code and binary: reveals, epistemics, rights, policy, disclosure.
3. Never spoil the long-form story — in text, audio **or** visuals.
4. Never make a claim stronger than its source.
5. Real case material first; original audio only when rights allow; the narrator stops for important original speech.
6. Each platform gets its own variant; each language gets native copy, not translation.
7. Shorts must feel like the same premium brand — never a clip farm.
8. Fereidoun is the host signature but is not in every clip.
9. Track conversion to long-form, not views; unavailable attribution stays "unavailable".
10. Human approval before anything is scheduled or published; export packages first, platform APIs last.
11. Loop until it works (A), then re-verify everything so far (B); at the end, loop the whole suite to 3 green runs. Never weaken tests.
12. I set counts and lengths in the UI; defaults are pre-filled; the UI matters as much as the backend.
13. Work phase by phase, stop after each, and report honestly.