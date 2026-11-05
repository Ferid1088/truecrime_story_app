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

const words = (text: string, corrected: Record<number, { was: string; finglish: string }> = {}) =>
  text.split(" ").map((surface, n) => ({ n, surface, corrected: n in corrected, was: corrected[n]?.was ?? "", finglish: corrected[n]?.finglish ?? "", pending: n in corrected }));
const take = (take_no: number, active: boolean, source: "original" | "correction") => ({
  take_no, source, active, duration_s: 4.2, cost_eur: source === "correction" ? 0.0021 : 0, text: "", aliases: [], created_at: "2026-11-05T10:00:00Z",
  audio_url: `/api/documentary/versions/7/block-review/blocks/FA_B02_g01_01/takes/${take_no}/audio`,
});

test("a corrected block stays yellow and its corrected word red, a new take is voiced and ticked, and the person can tick the earlier take again", async ({ page }) => {
  let block2 = { block_id: "FA_B02_g01_01", position: 1, canonical_text: "دورِ تخت نبود.", voice_text: "دورِ تخت نبود.", draft_text: null as string | null, text: "دورِ تخت نبود.",
                 edited: false, needs_sending: false, words: words("دورِ تخت نبود."), takes: [take(1, true, "original")] };
  let block1 = { ...block2, block_id: "FA_B01_g01_01", position: 0, canonical_text: "او آمد.", voice_text: "او آمد.", text: "او آمد.", words: words("او آمد."),
                   takes: [{ ...take(1, true, "original"), audio_url: "/api/x" }] };
  const state = () => ({ exists: true, version_id: 7, language: "fa", status: "open", assembled_current: false, assembled_at: null, approved: false,
                         corrected_blocks: block2.edited ? 1 : 0, to_send: block2.needs_sending ? 1 : 0, assembled_audio_url: null, blocks: [block1, block2] });
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
  await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
  await page.route("**/api/documentary/versions/7/block-review", (r: Route) => r.fulfill(ok(state())));
  await page.route("**/api/documentary/versions/7/block-review/blocks/FA_B02_g01_01", async (r: Route) => {
    const body = r.request().postDataJSON() as { words: { n: number; surface?: string; finglish?: string }[] };
    const edit = body.words[0];
    block2 = { ...block2, edited: true, needs_sending: true, draft_text: "دُورِ تخت نبود.", text: "دُورِ تخت نبود.",
               words: words("دُورِ تخت نبود.", { [edit.n]: { was: "دورِ", finglish: edit.finglish ?? "" } }) };
    await r.fulfill(ok(block2));
  });
  await page.route("**/api/documentary/versions/7/block-review/blocks/FA_B02_g01_01/send", async (r: Route) => {
    block2 = { ...block2, needs_sending: false, draft_text: null, voice_text: block2.text, words: block2.words.map((w) => ({ ...w, pending: false })),
               takes: [take(1, false, "original"), take(2, true, "correction")] };
    await r.fulfill(ok(block2));
  });
  await page.route("**/api/documentary/versions/7/block-review/blocks/FA_B01_g01_01/send", async (r: Route) => {
    expect(r.request().postDataJSON()).toEqual({ again: true });                      // nothing was corrected: the same words, another take
    const n = block1.takes.length + 1;
    block1 = { ...block1, takes: [...block1.takes.map((t) => ({ ...t, active: false })), { ...take(n, true, "correction"), audio_url: `/api/x${n}` }] };
    await r.fulfill(ok(block1));
  });
  await page.route("**/api/documentary/versions/7/block-review/blocks/FA_B02_g01_01/takes/*/activate", async (r: Route) => {
    const no = Number(r.request().url().split("/takes/")[1].split("/")[0]);
    block2 = { ...block2, takes: block2.takes.map((t) => ({ ...t, active: t.take_no === no })) };
    await r.fulfill(ok(block2));
  });

  await page.goto("/documentary/1?tab=Pronunciation");
  await page.getByRole("button", { name: "Voice review" }).click();
  await expect(page.getByTestId("block-review")).toContainText("2 blocks · 0 corrected · 0 to send");
  await expect(page.getByTestId("block-FA_B02_g01_01")).toHaveAttribute("data-edited", "false");

  await page.getByTestId("block-FA_B02_g01_01").getByRole("button", { name: "Correct" }).click();
  await page.getByLabel("Persian word 0").fill("دُورِ");
  await page.getByLabel("Finglish of word 0").fill("dore");
  await page.getByRole("button", { name: "Save correction" }).click();
  const card = page.getByTestId("block-FA_B02_g01_01");
  await expect(card).toHaveAttribute("data-edited", "true");
  await expect(card.locator("[data-corrected=true]")).toHaveText(/دُورِ/);
  await expect(card.locator("[data-corrected=false]")).toHaveCount(2);
  await expect(page.getByTestId("block-review")).toContainText("1 corrected · 1 to send");

  await card.getByRole("button", { name: "Send to ElevenLabs" }).click();
  await expect(card.getByLabel("Use take 2 of FA_B02_g01_01")).toBeChecked();
  await expect(card.getByLabel("Use take 1 of FA_B02_g01_01")).not.toBeChecked();
  await expect(card).toHaveAttribute("data-edited", "true");                        // yellow for good
  await expect(card.locator("[data-corrected=true]")).toHaveCount(1);               // red for good

  await card.getByLabel("Use take 1 of FA_B02_g01_01").click();          // a controlled box: it turns on when the server has ticked the take
  await expect(card.getByLabel("Use take 1 of FA_B02_g01_01")).toBeChecked();
  await expect(card.getByLabel("Use take 2 of FA_B02_g01_01")).not.toBeChecked();
  await expect(card.locator("[data-active=false]")).toHaveCount(1);                 // the other take stays, marked not used

  const first = page.getByTestId("block-FA_B01_g01_01");                            // any block can be voiced again and again, corrected or not
  await first.getByRole("button", { name: "Voice again" }).click();
  await expect(first.getByLabel("Use take 2 of FA_B01_g01_01")).toBeChecked();
  await first.getByRole("button", { name: "Voice again" }).click();
  await expect(first.getByLabel("Use take 3 of FA_B01_g01_01")).toBeChecked();
  await expect(first.locator("[data-active=false]")).toHaveCount(2);
  await expect(first).toHaveAttribute("data-edited", "false");                       // a new take of the same words is not a correction
});
