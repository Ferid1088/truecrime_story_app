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
const ref = { surface: "دادگاه", finglish: "dādgāh-e", phonemes: ["d", "ā", "d", "g", "ā", "h", "e"], ipa: "dɒːdɡɒːhe", syllables: ["dād", "gā", "he"], stress: 1,
  morphology: [{ text: "دادگاه", kind: "stem" }], reference_irab: "دادْگاهِ", added_carrier: false, valid: true, problems: [] };
const word = (strategy: string, extra: Record<string, unknown> = {}) => ({
  block_id: "FA_B40_g03_01", n: 1, word: "دادگاه", start: 3, end: 9, sentence: "یه دادگاه عمومی", finglish: "dādgāh-e", reference: ref,
  ezafe: { ezafe_type: "E", grammatical_relation: "noun_adjective", phonological_class: "EZAFE_CONSONANT_H", evidence_type: "linguistic_inference",
           expected_reading: "dādgāh-e omumi", written: false, conflicts: [], confidence: 0.8, tts_form: "دادگاهِ" },
  risk: { level: "HIGH", score: 5, signals: [{ name: "final_consonant_h", weight: 2, evidence: "a consonant h ends the word" }] },
  decision: { strategy, basis: "HIGH risk: a consonant h ends the word", replacement: strategy === "keep" ? null : "دادگاهِ", validated: false, needs_review: false },
  tts_input: strategy === "keep" ? "دادگاه" : "دادگاهِ", audio: { start: 10, end: 14 },
  memory: [{ id: 80, surface: "دادگاه", status: "candidate", trust: "UNVERIFIED_CANDIDATE", next_word: "", sense: "", approved_pronunciation: null,
             interventions: [{ kind: "irab", replacement: "دادگاهٔ", status: "candidate", successes: 0, failures: 0 }] }],
  verification: "UNVERIFIED", ...extra });
const plan = { version_id: 7, case_id: 1, language: "fa", voice_id: "v", model_id: "eleven_v4", audio_url: "/api/documentary/versions/7/audio/narration",
  counts: { words: 3, LOW: 1, MEDIUM: 0, HIGH: 2, CRITICAL: 0, changed: 1, escalated: 0, reference_valid: 3, reference_checked: 3 },
  consistency: [{ word: "بررسی", kind: "word", readings: { barresi: ["b1", "b2"], barrasi: ["b3"] }, legitimate: "" },
                { word: "مرد", kind: "word", readings: { mard: ["b1"], mord: ["b2"] }, legitimate: "a homograph" }],
  words: [word("selective_marks"), word("keep", { n: 2, word: "عمومی", tts_input: "عمومی" })] };

test.describe("why words change", () => {
  test("shows the reference, the risk, the decision and the exact voice input of a word, and filters the words", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
    await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
    await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
    await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
    await page.route("**/api/documentary/versions/7/pronunciation-plan?**", (r: Route) => r.fulfill(ok(plan)));
    await page.goto("/documentary/1?tab=Pronunciation");
    await page.getByRole("button", { name: "Why words change" }).click();
    const detail = page.getByLabel("Word decision");
    await expect(detail.getByText("دادْگاهِ")).toBeVisible();
    await expect(detail.getByText("validator: valid")).toBeVisible();
    await expect(detail.getByText(/EZAFE_CONSONANT_H/)).toBeVisible();
    await expect(detail.getByText(/final_consonant_h/)).toBeVisible();
    await expect(detail.getByText("original: دادگاه")).toBeVisible();
    await expect(detail.getByText("UNVERIFIED", { exact: true })).toBeVisible();
    await expect(detail.getByRole("button", { name: "Play the sentence" })).toBeVisible();
    await expect(page.getByLabel("Words of the plan").getByRole("button")).toHaveCount(1);
    await page.getByText("Read differently across blocks (1 words; names: 0)").click();
    await expect(page.getByText("barresi ×2 / barrasi ×1")).toBeVisible();
    await page.getByRole("button", { name: /High risk, kept/ }).click();
    await expect(page.getByLabel("Words of the plan").getByRole("button")).toHaveCount(1);
    await expect(detail.getByText("unchanged")).toBeVisible();
  });
});
