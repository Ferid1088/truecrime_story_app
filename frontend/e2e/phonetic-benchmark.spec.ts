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

const candidates = [{ key: "ahd", irab: "اَهد", ipa: "æhd" }, { key: "ohd", irab: "اُهد", ipa: "ohd" }];
const sample = (status: string, human: string | null) => ({
  id: 1, surface: "عهد", sentence: "از عهد حرف زد", category: "short_vowel", source: "case_audio", speaker: "", speed: "film", status, candidates,
  expected_key: "ahd", consensus_key: null, human_key: human, case_id: 15, block_id: "FA_B1",
  opinions: [{ kind: "agent", actor: "listener-1", choice: "ahd", confidence: 0.8 }, { kind: "recognizer", actor: "wav2vec2", choice: "ohd", confidence: 0.2 }],
});
const report = { counts: { disputed: 1 }, total: 1, by_category: { short_vowel: 1 }, by_source: { case_audio: 1 }, human_verified: 0, against_human_labels: {},
  consensus_checked_by_a_person: { clips: 0, consensus_right: 0 }, enough_for_a_verdict: false };

test.describe("the phonetic benchmark", () => {
  test("separates agent opinions from a person's label, which needs a name", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
    await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
    await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
    await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
    let current = sample("disputed", null);
    const posted: unknown[] = [];
    await page.route("**/api/documentary/phonetic/samples?**", (r: Route) => r.fulfill(ok({ samples: [current], report })));
    await page.route("**/api/documentary/phonetic/samples/1/peaks", (r: Route) => r.fulfill(ok({ peaks: Array.from({ length: 40 }, (_, i) => (i % 5) / 5) })));
    await page.route("**/api/documentary/phonetic/samples/1/audio", (r: Route) => r.fulfill({ status: 200, contentType: "audio/wav", body: "" }));
    await page.route("**/api/documentary/phonetic/samples/1/label", (r: Route) => {
      posted.push(r.request().postDataJSON());
      current = sample("human_verified", "ahd");
      return r.fulfill(ok(current));
    });
    await page.goto("/documentary/1?tab=Pronunciation");
    await page.getByRole("button", { name: "Phonetic benchmark" }).click();
    await expect(page.getByText("Agent consensus is not ground truth")).toBeVisible();
    await expect(page.getByText("Not enough labels for a verdict")).toBeVisible();
    await expect(page.getByText("Recognizer", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /اَهد/ })).toBeDisabled();
    await page.getByLabel("Reviewer name").fill("owner");
    await expect(posted).toEqual([]);
    await page.getByRole("button", { name: /اَهد/ }).click();
    await expect(page.getByText("Verified by ear").first()).toBeVisible();
    expect(posted).toEqual([{ choice: "ahd", reviewer: "owner" }]);
  });

  test("a controlled clip is labelled blind: expected and intended readings and opinions appear only after the label", async ({ page }) => {
    const blind = { ...sample("unlabeled", null), source: "controlled", purpose: "validation", blind: true, expected_key: null, intended_key: null, opinions: [] };
    const labelled = { ...sample("human_verified", "ohd"), source: "controlled", purpose: "validation", blind: false, intended_key: "ohd" };
    let current: object = blind;
    await mockApi(page);
    await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
    await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
    await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
    await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
    await page.route("**/api/documentary/phonetic/samples?**", (r: Route) => r.fulfill(ok({ samples: [current], report })));
    await page.route("**/api/documentary/phonetic/samples/1/peaks", (r: Route) => r.fulfill(ok({ peaks: [0.1, 0.2] })));
    await page.route("**/api/documentary/phonetic/samples/1/audio", (r: Route) => r.fulfill({ status: 200, contentType: "audio/wav", body: "" }));
    await page.route("**/api/documentary/phonetic/samples/1/label", (r: Route) => { current = labelled; return r.fulfill(ok(labelled)); });
    await page.goto("/documentary/1?tab=Pronunciation");
    await page.getByRole("button", { name: "Phonetic benchmark" }).click();
    await expect(page.getByLabel("Blind labelling")).toBeVisible();
    await expect(page.getByText("Hidden until you label.")).toBeVisible();
    await expect(page.getByLabel("After labelling")).toHaveCount(0);
    await page.getByLabel("Reviewer name").fill("owner");
    await page.getByRole("button", { name: /اُهد/ }).click();
    await expect(page.getByLabel("After labelling")).toContainText("the voice was told to say ohd");
  });
});
