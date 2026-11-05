import type { Route } from "@playwright/test";
import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const settings = {
  languages: ["en", "fa"], film_minutes: { min: 45, max: 120 }, pilot_seconds: 120, voices: {}, channels: { en: { name: "ClueVera" } }, avatar_generation: { enabled: true },
  pronunciation_check: { enabled: false, languages: [], max_rounds: 1 }, voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: [], publish: [] }, dynamic_eq: {},
};
const overview = { finals: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null, audio_plan: null, languages: { fa: { version_id: 7 } }, visual_plan: null, jobs: [], visual_counts: {} };
const costs = { case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] };
const ok = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const block = {
  block_id: "FA_B01_g01_01", canonical: "کارآگاه آرام وارد اتاق شد.", tts_text: "کارآگاه، آرام وارد اتاق شد.", changed: true, review: "PASS", rounds: 1, refused: [], flags: [],
  checks: [{ round: 1, decision: "PASS", issues: [] }],
  edits: [{ after_word: 0, old: "", new: "،", reason: "the subject ends: آرام tells how he entered", source: "agent", canonical_offset: 7 }],
  boundaries: [
    { id: "b0", after_word: 0, words: ["کارآگاه", "آرام"], status: "EZAFE_FORBIDDEN", reason: "he entered quietly", source: "syntax_agent", relation: "subject_adverb",
      pronunciation: "", span: [0, 12], punctuation_can_clarify: true, needs_phonetic_guidance: false },
    { id: "b2", after_word: 2, words: ["وارد", "اتاق"], status: "EZAFE_CONTEXT_DEPENDENT", reason: "the meaning decides", source: "none", relation: "",
      pronunciation: "", span: [13, 21], punctuation_can_clarify: false, needs_phonetic_guidance: false },
  ],
};
const summary = {
  source: "the last narration run",
  statistics: { blocks: 1, blocks_unchanged: 0, commas_inserted: 1, marks_replaced_or_removed: 0, ezafe_required: 0, ezafe_forbidden: 1, context_dependent: 1,
                reviewer_rejections: 0, source_integrity_violations: 0 },
  blocks: [{ block_id: block.block_id, changed: true, edits: 1, review: "PASS", flags: 0, boundaries: { EZAFE_FORBIDDEN: 1, EZAFE_CONTEXT_DEPENDENT: 1 } }],
};

test("the punctuation view shows the approved text, the voice text, the change with its reason and the ezafe decisions", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
  await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
  await page.route("**/api/documentary/versions/7/semantic-punctuation?**", (r: Route) => r.fulfill(ok(summary)));
  await page.route("**/api/documentary/versions/7/semantic-punctuation/FA_B01_g01_01?**", (r: Route) => r.fulfill(ok(block)));
  await page.goto("/documentary/1?tab=Pronunciation");
  await page.getByRole("button", { name: "Punctuation" }).click();
  await expect(page.getByLabel("Punctuation summary")).toContainText("1 commas inserted");
  const detail = page.getByLabel("Punctuation of the block");
  await expect(detail.getByLabel("Original Persian")).toHaveText("کارآگاه آرام وارد اتاق شد.");
  await expect(detail.getByLabel("Prepared for ElevenLabs")).toContainText("کارآگاه،");
  await expect(detail.getByLabel("Punctuation changes")).toContainText("آرام tells how he entered");
  await expect(detail.getByLabel("Ezafe decisions")).toContainText("EZAFE_FORBIDDEN");
  await expect(detail.getByLabel("Ezafe decisions")).toContainText("EZAFE_CONTEXT_DEPENDENT");
  await expect(detail.getByText("This layer is text only.")).toBeVisible();
});
