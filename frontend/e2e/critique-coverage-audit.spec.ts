import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test, expect } from "./test-base";
import { mockApi, PRODUCTION_LANGUAGES } from "./fixtures";

/** The Critique tab of a film made by the picture planner: its stored audit is the cut's coverage report, not the older picture audit
 * (a trimmed copy of a real production, so the tab is built from the data the pipeline really stores). */
const overview = JSON.parse(readFileSync(join(process.cwd(), "e2e/assets/production-coverage-audit.json"), "utf-8"));
const settings = {
  languages: [...PRODUCTION_LANGUAGES], film_minutes: { min: 45, max: 120 }, pilot_seconds: 120,
  voices: Object.fromEntries(PRODUCTION_LANGUAGES.map((language) => [language, { voice_id: `voice-${language}`, model_id: "eleven_v4", language_code: language, audio_tags: true }])),
  voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: [], publish: [] }, dynamic_eq: {},
};

test("the Critique tab shows the coverage audit of a film and does not crash on it", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await mockApi(page);
  await page.route("**/api/documentary/settings", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(settings) }));
  await page.route("**/api/cases/1/documentary?**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(overview) }));
  await page.route("**/api/cases/1/documentary", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(overview) }));

  await page.goto("/documentary/1?tab=Critique");

  await expect(page.getByText("Coverage audit")).toBeVisible();
  await expect(page.getByText("Something went wrong")).toHaveCount(0);
  expect(errors).toEqual([]);
});
