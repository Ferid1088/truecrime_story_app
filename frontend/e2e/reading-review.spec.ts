import type { Route } from "@playwright/test";
import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const settings = {
  languages: ["en", "fa"],
  film_minutes: { min: 45, max: 120 },
  pilot_seconds: 120,
  voices: {},
  channels: { en: { name: "ClueVera" } },
  avatar_generation: { enabled: true },
  pronunciation_check: { enabled: false, languages: [], max_rounds: 1 },
  voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: [], publish: [] },
  dynamic_eq: {},
};
const overview = {
  finals: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null, audio_plan: null,
  languages: { fa: { version_id: 7 } }, visual_plan: null, jobs: [], visual_counts: {},
};
const costs = { case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] };
const ok = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const doubt = (id: number, surface: string, editor: string, checker: string): Record<string, unknown> => ({
  id, block_id: "FA_B01_g01_01", word_n: id, surface, sentence: `سارا ${surface} گفت`, question: "which reading?",
  editor_latin: editor, checker_latin: checker, status: "open", answer_latin: null,
});

test.describe("the reading review", () => {
  test("lists every word in doubt, saves all the answers at once and shows them answered", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
    await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
    await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
    let rows = [doubt(1, "بهم", "beham", "baham"), doubt(2, "ملک", "melk", "malek")];
    const posted: unknown[] = [];
    await page.route("**/api/documentary/versions/7/reading-doubts", (r: Route) => {
      if (r.request().method() === "POST") {
        const body = r.request().postDataJSON() as { answers: { id: number; choice: string; latin?: string }[] };
        posted.push(body);
        rows = rows.map((d) => {
          const a = body.answers.find((x) => x.id === d.id);
          return a ? { ...d, status: "answered", answer_latin: a.choice === "custom" ? a.latin! : a.choice === "checker" ? d.checker_latin : d.editor_latin } : d;
        });
      }
      const open = rows.filter((d) => d.status === "open").length;
      return r.fulfill(ok({ version_id: 7, language: "fa", open, answered: rows.length - open, doubts: rows }));
    });
    await page.goto("/documentary/1?tab=Reading%20Review");
    await expect(page.getByText("Reading review — Persian")).toBeVisible();     // opens on the language that has a narration, no click needed
    await expect(page.getByTestId("reading-doubt")).toHaveCount(2);
    await expect(page.getByText("2 open · 0 answered")).toBeVisible();
    const first = page.getByTestId("reading-doubt").nth(0);
    const second = page.getByTestId("reading-doubt").nth(1);
    await first.getByRole("button", { name: /Checker/ }).click();
    await first.getByLabel("keep for every film").check();
    await second.getByRole("button", { name: "My reading" }).click();
    const save = page.getByRole("button", { name: /Save/ });
    await expect(save).toHaveText("Save 1 answer");
    await second.getByLabel("Your reading of ملک").fill("malek");
    await expect(save).toHaveText("Save 2 answers");
    await save.click();
    await expect.poll(() => posted).toEqual([{ answers: [
      { id: 1, choice: "checker", everywhere: true }, { id: 2, choice: "custom", latin: "malek", everywhere: false }] }]);
    await expect(page.getByText("0 open · 2 answered")).toBeVisible();
    await expect(page.getByText("Every word is answered. Resume the job in the Run tab to voice the film.")).toBeVisible();
  });
});
