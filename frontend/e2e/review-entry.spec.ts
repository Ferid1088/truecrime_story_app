import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";
import type { Page, Route } from "@playwright/test";

const settings = {
  languages: ["en", "de", "fa", "ar"],
  film_minutes: { min: 45, max: 120 },
  pilot_seconds: 120,
  voices: {},
  pronunciation_check: { enabled: false, languages: [], max_rounds: 1 },
  voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: [], publish: [] },
  dynamic_eq: {},
};

const overview = {
  finals: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null,
  audio_plan: null, languages: {}, visual_plan: null, jobs: [], visual_counts: {},
};

const fulfil = (body: unknown, status = 200) => ({
  status, contentType: "application/json", body: JSON.stringify(body),
});

async function mockPage(page: Page) {
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) =>
    r.fulfill(fulfil(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) =>
    r.fulfill(fulfil(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) =>
    r.fulfill(fulfil({ case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] })));
}

test.describe("Review entry point and video pieces", () => {
  test("the case page has a Review pictures button with the count, leading to the queue", async ({ page }) => {
    await mockPage(page);
    await page.goto("/documentary/1");
    const button = page.getByTestId("review-pictures-button");
    await expect(button).toHaveText("Review pictures (2)");
    await button.click();
    await expect(page).toHaveURL(/\/documentary\/1\/review$/);
    await expect(page.getByTestId("review-queue-page")).toBeVisible();
  });

  test("a review_youtube_clips need links to the review queue", async ({ page }) => {
    await mockApi(page);
    const RUN = {
      id: 9, status: "waiting_owner", stage: "publish", error: null,
      created_at: "2026-10-21T10:00:00Z", updated_at: "2026-10-21T10:05:00Z", completed_at: null,
      case: { id: 1, title: "A case" },
      estimate: { languages: ["en"], cap_eur: 300, estimated_eur: 10, visual_research_eur: 1,
        range_eur: { low: 8, high: 12 }, pricing_last_verified: "2026-10-01", price_warnings: [] },
      budget: { cap_eur: 300, spent_eur: 12.5 },
      steps: [
        { name: "case", status: "done", detail: null, at: null },
        { name: "research", status: "done", detail: null, at: null },
        { name: "texts", status: "done", detail: null, at: null },
        { name: "films", status: "done", detail: null, at: null },
        { name: "titles", status: "done", detail: null, at: null },
        { name: "shorts", status: "done", detail: null, at: null },
        { name: "publish", status: "waiting", detail: null, at: null },
      ],
      needs_you: [
        { key: "publish:en:review_youtube_clips", kind: "review_youtube_clips",
          message: "Needs you: review the YouTube clips used in this film (en): VIS_900",
          language: "en", blocking: true },
      ],
      languages: { en: { film_job: null, titles: [] } },
      publish_requests: 0,
    };
    await page.route("**/api/autopilot/runs/9", (r: Route) => r.fulfill(fulfil(RUN)));
    await page.route("**/api/autopilot/runs/9/publish-requests", (r: Route) =>
      r.fulfill(fulfil({ sent: 0, requests: [] })));
    await page.goto("/autopilot/9");
    const need = page.getByTestId("needs-you");
    await expect(need).toContainText("VIS_900");
    const link = need.getByRole("link", { name: "Open the review queue" });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/documentary/1/review");
  });
});
