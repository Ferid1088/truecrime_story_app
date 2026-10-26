import type { Page, Route } from "@playwright/test";
import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

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
const costs = { case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] };
const ok = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function mockPage(page: Page) {
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
}

const REVIEW = {
  id: 7, case_id: 1, language: "en", state: "AWAITING_OWNER_REVIEW", production_script_id: 3,
  render_path: "/films/en.mp4", render_sha256: "ab".repeat(32),
  validation: {
    review_ready: true, blocking: [], publication_blockers: ["2 requirement(s) recorded as shortage"],
    coverage: { temporal_coverage: 0.991, black_seconds: 0.4, weak_sentence_count: 3 },
    file: { duration: 3547, width: 1920, height: 1080, fps: 25, video_codec: "h264", audio_codec: "aac",
      black_seconds: 0.4, integrated_lufs: -16.2 },
  },
  owner_note: null, created_at: "2026-10-26T10:00:00Z", decided_at: null,
};

test.describe("Owner film review", () => {
  test("a film awaiting review shows its checks and the owner can approve the exact file", async ({ page }) => {
    await mockPage(page);
    let decision: Record<string, unknown> | null = null;
    await page.route("**/api/cases/1/mvp/reviews", (r: Route) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([REVIEW]) }));
    await page.route("**/api/mvp/reviews/7/decision", (r: Route) => {
      decision = r.request().postDataJSON();
      return r.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ ...REVIEW, state: "APPROVED" }) });
    });
    await page.goto("/documentary/1");
    await page.getByRole("tab", { name: "Visual Planner" }).click();
    const card = page.getByTestId("film-review-en");
    await expect(card.getByTestId("film-review-state")).toHaveText("AWAITING_OWNER_REVIEW");
    await expect(card).toContainText("1920×1080 @ 25 fps");
    await expect(card).toContainText("Temporal coverage 99.1%");
    await expect(card).toContainText("blocks publication, not this review");
    await card.getByRole("button", { name: "Approve this exact film" }).click();
    await expect.poll(() => decision).toMatchObject({ decision: "APPROVED", render_sha256: "ab".repeat(32) });
  });

  test("a film that is not awaiting review offers no decision buttons", async ({ page }) => {
    await mockPage(page);
    await page.route("**/api/cases/1/mvp/reviews", (r: Route) =>
      r.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify([{ ...REVIEW, state: "PRODUCING" }]) }));
    await page.goto("/documentary/1");
    await page.getByRole("tab", { name: "Visual Planner" }).click();
    await expect(page.getByTestId("film-review-en")).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve this exact film" })).toHaveCount(0);
  });
});
