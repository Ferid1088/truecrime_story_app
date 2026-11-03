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
const summary = { name: "p1", title: "Case 15 preview", case_id: 15, language: "fa", created_at: "2026-11-04T10:00:00", duration_seconds: 125, cost: { characters_sent: 46279, requests: 236, actual_eur: 3.31, cap_eur: 10, spent_before_eur: 199.9, spent_after_eur: 203.2, case_cap_eur: 300 } };

test.describe("the narration preview", () => {
  test("plays one narration in story order and jumps to a block", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
    await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
    await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
    await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
    await page.route("**/api/documentary/previews", (r: Route) => r.fulfill(ok([summary])));
    await page.route("**/api/documentary/previews/p1", (r: Route) =>
      r.fulfill(ok({ ...summary, story_version_id: 7, voice_id: "v", model_id: "eleven_v4", source: "risk engine + planner", blocks: [
        { block_id: "B1", start: 0, end: 60, text: "بلوک اول" }, { block_id: "B2", start: 60, end: 125, text: "بلوک دوم" }] })));
    await page.route("**/api/documentary/previews/p1/audio", (r: Route) => r.fulfill({ status: 200, contentType: "audio/mpeg", body: "" }));
    await page.goto("/documentary/1?tab=Pronunciation");
    await page.getByRole("button", { name: "Narration preview" }).click();
    await expect(page.getByText("Case 15 preview")).toBeVisible();
    await expect(page.getByText(/46,279 characters/)).toBeVisible();
    await expect(page.getByLabel("Narration preview")).toHaveAttribute("src", /previews\/p1\/audio$/);
    const blocks = page.getByLabel("Blocks in story order").getByRole("button");
    await expect(blocks).toHaveCount(2);
    await expect(blocks.first()).toContainText("بلوک اول");
  });
});
