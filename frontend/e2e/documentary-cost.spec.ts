import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const settings = {
  languages: ["en", "de", "fa", "ar"],
  film_minutes: { min: 45, max: 120 },
  pilot_seconds: 120,
  voices: Object.fromEntries(["en", "de", "fa", "ar"].map((language) => [language, {
    voice_id: `voice-${language}`, model_id: "eleven_v3", language_code: language, audio_tags: true,
  }])),
  pronunciation_check: { enabled: false, languages: [], max_rounds: 1 },
  voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: ["owned", "licensed", "public_domain", "creative_commons", "editorial_review_required"], publish: ["owned", "licensed", "public_domain", "creative_commons"] },
  dynamic_eq: {},
};

const overview = {
  masters: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null, audio_plan: null,
  languages: { en: null, de: null, fa: null, ar: null }, visual_plan: null, jobs: [], visual_counts: {},
};

function dryRun(languages: string[], pilotSeconds: number) {
  const factor = languages.length * pilotSeconds / 120;
  const expected = 0.14 * factor;
  return {
    dry_run: true, case_id: 1, languages, cap_eur: 20, estimated_cost_eur: Number(expected.toFixed(2)),
    estimate_range_eur: { low: Number((0.11 * factor).toFixed(2)), expected: Number(expected.toFixed(2)), high: Number((0.17 * factor).toFixed(2)) },
    within_cap: true, stop_stage_if_cap_hit: null, provider_calls: 8, sent: false,
    pricing_last_verified: "2026-10-13", pilot_seconds: pilotSeconds, price_warnings: [], token_assumptions: {}, token_formulas: {}, metrics: {}, stages: [],
  };
}

async function mockDocumentary(page: import("@playwright/test").Page) {
  await mockApi(page);
  await page.route("**/api/documentary/settings", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(settings) }));
  await page.route("**/api/cases/1/documentary", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(overview) }));
  await page.route("**/api/cases/1/costs", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ case_id: 1, cap_eur: 20, spent_eur: 0, remaining_eur: 20, status: "new", stop_reason: null, calls: [] }) }));
  await page.route("**/api/cases/1/documentary/dry-run", (route) => {
    const body = route.request().postDataJSON() as { languages: string[]; pilot_seconds: number };
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(dryRun(body.languages, body.pilot_seconds)) });
  });
}

async function selectEnglishOnly(page: import("@playwright/test").Page) {
  for (const language of ["German", "Persian", "Arabic"])
    await page.getByRole("checkbox", { name: language, exact: true }).uncheck();
}

test.describe("Documentary cost preflight", () => {
  test("shows cap, spent-so-far, warnings, and the 120-second estimate", async ({ page }) => {
    await mockDocumentary(page);
    await page.goto("/documentary/1");
    await selectEnglishOnly(page);
    await expect(page.getByTestId("pilot-estimate")).toContainText("expected €0.14");
    const budget = page.getByTestId("preflight-budget");
    await expect(budget).toContainText("cap €20.00");
    await expect(budget).toContainText("Spent so far €0.0000");
    await expect(budget).toContainText("Warnings: none");
    await expect(page.getByRole("button", { name: "Start pilot" })).toBeVisible();
  });

  test("changes the estimate for language count and pilot length", async ({ page }) => {
    await mockDocumentary(page);
    await page.goto("/documentary/1");
    await selectEnglishOnly(page);
    const estimate = page.getByTestId("pilot-estimate");
    await expect(estimate).toContainText("expected €0.14");
    for (const language of ["German", "Persian", "Arabic"]) await page.getByRole("checkbox", { name: language, exact: true }).check();
    await expect(estimate).toContainText("expected €0.56");
    await page.getByRole("spinbutton", { name: "Pilot length (seconds)" }).fill("1800");
    await expect(estimate).toContainText("expected €8.40");
  });
});
