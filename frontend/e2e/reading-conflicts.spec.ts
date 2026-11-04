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
const family = { word: "بررسی", kind: "word", status: "unresolved", rule: "", reading: "", evidence: "", candidates: { barresi: 15, barrasi: 1 }, occurrences: 16,
  forms: ["بررسی"], why_undecided: "every candidate can be written on the letters",
  evidence_per_candidate: { barresi: { written_on_the_letters: true, blocks: 15, contexts: [{ block: "b1", sentence: "پرونده رو بررسی کردن." }] },
                            barrasi: { written_on_the_letters: true, blocks: 1, contexts: [] } } };
const review = { resolved: [{ ...family, word: "کالب", kind: "name", status: "resolved", rule: "source_syllables", reading: "kāleb",
  evidence: "the source name 'Caleb' has 2 syllable(s)", candidates: { kāleb: 115, kālb: 31 } }], retained: [], owner_review: [family] };

test.describe("conflicting readings", () => {
  test("lists what the rules resolved, asks one decision per family, and needs a named reviewer", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
    await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
    await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
    await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
    await page.route("**/api/documentary/versions/7/reading-review?**", (r: Route) => r.fulfill(ok(review)));
    const posted: unknown[] = [];
    await page.route("**/api/documentary/versions/7/reading-resolutions?**", (r: Route) => {
      posted.push(r.request().postDataJSON());
      return r.fulfill(ok({ word: "بررسی", reading: "barresi", rows_saved: 1 }));
    });
    await page.goto("/documentary/1?tab=Pronunciation");
    await page.getByRole("button", { name: "Conflicting readings" }).click();
    await expect(page.getByText("1 resolved by a rule · 0 kept as legitimate variants · 1 for you")).toBeVisible();
    const fam = page.getByLabel("Family بررسی");
    await expect(fam.getByText("16 occurrences")).toBeVisible();
    await expect(fam.getByText("پرونده رو بررسی کردن.")).toBeVisible();
    await expect(fam.getByRole("button", { name: "Read it barresi" })).toBeDisabled();
    await page.getByLabel("Reviewer name").fill("owner");
    await fam.getByRole("button", { name: "Read it barresi" }).click();
    await expect.poll(() => posted).toEqual([{ word: "بررسی", reading: "barresi", reviewer: "owner" }]);
    await page.getByText("Resolved by a rule (1)").click();
    await expect(page.getByText(/kāleb \(source_syllables\)/)).toBeVisible();
  });
});
