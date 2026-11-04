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

const word = {
  n: 2, word: "عمر", audio_window: { start: 1.2, end: 1.5 }, expected: "omr", heard: "o m e r",
  candidates: [{ reading: "omr", kind: "expected", ipa: "omɾ", loss: { "1.0": 2.1, "0.8": 2.0 } }, { reading: "omer", kind: "epenthesis", ipa: "omeɾ", loss: { "1.0": 1.4, "0.8": 1.5 } }],
  suggested: "FAIL", status: "UNCERTAIN", evidence: ["heard as omer (epenthesis): it fits better by 0.7 per frame", "advisory: the recognizer has not passed its validation"],
  model_validation: "UNVALIDATED", advisory: true,
};
const proposal = {
  block_id: "FA_B30_g03_01", word_id: 2, word: "عمر", suspected_root_cause: "tts_misreading", evidence: ["the reading was right; the voice said omer (epenthesis)"],
  verified_target_reading: "omr", verified_target_phonemes: "omɾ", estimated_characters: 120, estimated_eur: 0.0033, status: "PROPOSED", attempts: [],
  strategies: [{ type: "targeted_diacritics", form: "عُمر", tts_text: "...", reason: "short vowels written on the word's own letters" },
               { type: "inline_ipa", form: "/omɾ/", tts_text: "...", reason: "the sounds themselves" }],
};
const run = {
  summary: { run_id: "20261104T190000", created_at: "2026-11-04T19:00:00", words: 2, blocks_reviewed: 1, statuses: { UNCERTAIN: 1, NOT_EVALUABLE: 1 },
             suggested: { FAIL: 1, NOT_EVALUABLE: 1 }, proposals: 1, advisory: true, model_validation: { validated: false, labelled_clips: 0 },
             voice_text_differs_from_approved: { FA_B30_g03_01: ["کالب → کیلب"] }, spent_by_run_eur: 0, sources: { notes: [] } },
  alignment_summary: { blocks: { ALIGNED: 1 }, sentences: { ALIGNED: 2 }, calibrated: false, verified_by_agent: false },
  blocks: [{ block_id: "FA_B30_g03_01", text: "کالب برای اشلی یه بیمه‌نامه‌ی عمر گرفته بود.", voice_text: null, has_take: true,
             alignment: { status: "ALIGNED", english_text: "Caleb had taken out a life insurance policy on Ashley.", issues: [], evidence: [], confidence: null,
                          confidence_note: "uncalibrated: no reviewed alignment exists yet" },
             voice_text_differs: ["کالب → کیلب"], counts: { FAIL: 1, NOT_EVALUABLE: 1 }, flagged: [word], proposals: [proposal] }],
};

test.describe("audio review", () => {
  test("shows the English passage, an advisory suspected error with its evidence, and a repair the owner must authorize", async ({ page }) => {
    let repaired: unknown = null;
    await mockApi(page);
    await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
    await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
    await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
    await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
    await page.route("**/api/documentary/versions/7/ppie-review", (r: Route) => r.fulfill(ok({
      narrations: [{ label: "ppie", folder_exists: true, runs: ["20261104T190000"] }], validation: { validated: false, labelled_clips: 0 } })));
    await page.route("**/api/documentary/versions/7/ppie-review/ppie/20261104T190000", (r: Route) => r.fulfill(ok(run)));
    await page.route("**/proposals/FA_B30_g03_01/2/generate", (r: Route) => { repaired = r.request().postDataJSON(); return r.fulfill(ok({})); });
    await page.goto("/documentary/1?tab=Pronunciation");
    await page.getByRole("button", { name: "Audio review" }).click();
    await expect(page.getByLabel("Recognizer validation")).toContainText("every decision is advisory");
    const block = page.getByLabel("Block review");
    await expect(block.getByLabel("English passage")).toContainText("life insurance policy");
    await expect(block.getByLabel("Not the approved text")).toContainText("کالب → کیلب");
    await expect(block.getByText("suspected error (advisory)")).toBeVisible();
    await expect(block.getByText(/heard as omer/)).toBeVisible();
    const repair = block.getByLabel("Repair of عمر");
    await expect(repair.getByText("tts_misreading")).toBeVisible();
    await expect(repair.getByRole("button", { name: "Voice this block again" }).first()).toBeDisabled();
    await page.getByLabel("Reviewer name").fill("owner");
    await repair.getByRole("button", { name: "Voice this block again" }).nth(1).click();
    await expect.poll(() => repaired).toEqual({ strategy: 1, max_eur: 0.01, by: "owner" });
  });
});
