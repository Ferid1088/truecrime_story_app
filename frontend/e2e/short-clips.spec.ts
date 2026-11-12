import type { Route } from "@playwright/test";
import { test, expect } from "./test-base";
import { mockApi, PRODUCTION_LANGUAGES } from "./fixtures";

const ok = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const settings = {
  languages: [...PRODUCTION_LANGUAGES], film_minutes: { min: 45, max: 120 }, pilot_seconds: 120, voices: {}, channels: { en: { name: "ClueVera" } }, avatar_generation: { enabled: true },
  voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: [], publish: [] }, dynamic_eq: {},
};
const overview = { finals: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null, audio_plan: null, languages: {}, visual_plan: null, jobs: [], visual_counts: {} };
const costs = { case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] };
const parts = (hook: string) => ({ hook, context: "Context line.", detail: "Detail line.", reaction: "A question?", cta: "Watch the full film." });
const clip = (clip_key: string, angle: string, hook: string, extra: object = {}) =>
  ({ clip_key, angle, kind: "master", status: "valid", parts: parts(hook), voiced: false, voice_seconds: null, review: null, video: null, ...extra });
const CLIPS = {
  languages: [
    { language: "en", clips: [
      clip("C1", "question", "Somebody broke in.", { voiced: true, voice_seconds: 30, video: { seconds: 26, bytes: 1000, url: "/api/cases/1/short-clips/en/C1/video" } }),
      clip("C2", "evidence", "The console was open."),
    ] },
    { language: "fa", clips: [clip("C1", "question", "یه نفر اومده تو خونه‌م.", { voiced: true, voice_seconds: 29, review: "finished" })] },
  ],
};

test("Short clips is a tab that lists every language with the parts, the voice state and the video", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
  await page.route("**/api/cases/1/short-clips", (r: Route) => r.fulfill(ok(CLIPS)));
  await page.goto("/documentary/1");
  await expect(page.getByRole("tab", { name: "Host" })).toBeVisible();
  const tabs = await page.getByRole("tab").allInnerTexts();
  expect(tabs.indexOf("Short clips")).toBe(tabs.indexOf("Picture review") + 1);
  await page.getByRole("tab", { name: "Short clips" }).click();
  await expect(page.getByTestId("short-clips-summary")).toHaveText("2 clips · 1 voiced · 1 with a video");
  const first = page.getByTestId("short-clip-C1");
  await expect(first).toContainText("Somebody broke in.");
  await expect(first.getByTestId("short-clip-voice")).toHaveText("voiced 30 s");
  await expect(first.getByTestId("short-clip-video-state")).toHaveText("video 26 s");
  await expect(first.locator("video")).toHaveCount(1);
  const second = page.getByTestId("short-clip-C2");
  await expect(second.getByTestId("short-clip-voice")).toHaveText("not voiced yet");
  await expect(second.getByTestId("short-clip-video-state")).toHaveText("no video yet");
  await expect(second.locator("video")).toHaveCount(0);
  await page.getByTestId("short-clips-language-fa").click();
  await expect(page.getByTestId("short-clips-summary")).toHaveText("1 clips · 1 voiced · 0 with a video");
  await expect(page.getByTestId("short-clip-C1").getByTestId("short-clip-review")).toHaveText("review finished");
  await expect(page.getByText("یه نفر اومده تو خونه‌م.")).toHaveAttribute("dir", "rtl");
});

test("Short clips says so when the case has none", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
  await page.route("**/api/cases/1/short-clips", (r: Route) => r.fulfill(ok({ languages: [] })));
  await page.goto("/documentary/1?tab=Short%20clips");
  await expect(page.getByText("No short clips yet")).toBeVisible();
});
