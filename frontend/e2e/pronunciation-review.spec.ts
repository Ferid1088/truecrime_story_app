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

const entry = (id: number, surface: string, expected: string, status = "legacy_unverified"): Record<string, unknown> => ({
  id, language: "fa", surface, surface_key: surface, sense: "", scope: "global", prev_word: "", next_word: "", voice_id: "", model_id: "", status, source: "legacy",
  expected_irab: expected, approved_pronunciation: null, approved_ipa: null, observed_wrong: "", sentence: "", confidence: 0, reviewer: null, approved_at: null, version: 1,
  origin: "reading_approvals", different_readings: [],
  sources: [{ table: "reading_approvals", id, source: "owner", kind: "spoken_form", latin: "'ahd", created_at: null }],
});
const words = ["از", "عهدهایی", "حرف", "زد"].map((text, i) => ({ text, start: i * 0.5, end: i * 0.5 + 0.4 }));

test.describe("the pronunciation review", () => {
  test("shows the imported entries as unverified, needs a named reviewer, and approves only on the explicit action", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
    await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
    await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
    let rows = [entry(1, "عهد", "اَهد"), entry(2, "مسلح", "مُسَللَح")];
    const posted: unknown[] = [];
    await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => {
      const status = new URL(r.request().url()).searchParams.get("status");
      const counts: Record<string, number> = {};
      rows.forEach((e) => { counts[e.status as string] = (counts[e.status as string] ?? 0) + 1; });
      return r.fulfill(ok({ counts, entries: rows.filter((e) => !status || e.status === status) }));
    });
    await page.route("**/api/documentary/pronunciation/entries/1/occurrences?**", (r: Route) =>
      r.fulfill(ok({ audio_url: "/api/documentary/versions/7/audio/narration", total: 1, occurrences: [{ block_id: "FA_B52_g02_01", form: "exact", written: "عهد", start: 0.5, end: 0.9, hit: 1, words }] })));
    await page.route("**/api/documentary/pronunciation/clip-peaks?**", (r: Route) => r.fulfill(ok({ peaks: Array.from({ length: 40 }, (_, i) => (i % 5) / 5) })));
    await page.route("**/api/documentary/pronunciation/entries/1/approve", (r: Route) => {
      posted.push(r.request().postDataJSON());
      rows = rows.map((e) => (e.id === 1 ? { ...e, status: "human_approved", approved_pronunciation: "اَهد", reviewer: "owner" } : e));
      return r.fulfill(ok(rows[0]));
    });
    await page.goto("/documentary/1?tab=Pronunciation");
    await expect(page.getByText("Pronunciation review — Persian")).toBeVisible();
    await expect(page.getByRole("button", { name: /Legacy — unverified \(2\)/ })).toBeVisible();
    await expect(page.getByTestId("expected-irab")).toHaveText("اَهد");
    await expect(page.getByText("reading_approvals", { exact: false }).first()).toBeVisible();
    const occurrence = page.getByTestId("pronunciation-occurrence");
    await expect(occurrence).toHaveCount(1);
    await expect(occurrence.getByText("عهدهایی")).toBeVisible();
    await expect(page.getByRole("button", { name: "Play word" })).toBeVisible();
    await expect(page.getByText("Write your name above first")).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
    await page.getByLabel("Reviewer name").fill("owner");
    await page.getByLabel("Pronunciation of عهد").fill("اَهد");
    await page.getByLabel("IPA").fill("ʔæhd");
    await expect(posted).toEqual([]);                                                   // nothing is approved by looking at it
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect.poll(() => posted).toEqual([{ pronunciation: "اَهد", reviewer: "owner", scope: "global", ipa: "ʔæhd", supersede: false, reason: "" }]);
    await expect(page.getByRole("button", { name: /Legacy — unverified \(1\)/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Approved by you \(1\)/ })).toBeVisible();
  });
});
