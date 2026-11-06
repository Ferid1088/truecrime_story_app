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
  voice: { speed: 1.2, stability: 0.2, post_speed: 1.1 }, take_no, source, active, duration_s: 4.2, cost_eur: source === "correction" ? 0.0021 : 0, text: "", aliases: [], created_at: "2026-11-05T10:00:00Z",
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
  await page.getByRole("button", { name: "Voice review", exact: true }).click();
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
  await expect(card.getByTestId("voice-FA_B02_g01_01-2")).toHaveText("speed 1.2 · stability 0.2 · ×1.1");      // what the take was voiced with is shown
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

test("replace in all blocks shows the places first and only then corrects the blocks, which turn yellow with red words", async ({ page }) => {
  const mk = (n: number, text: string, hit = false) => ({
    block_id: `FA_B0${n}_g01_01`, position: n - 1, canonical_text: text, voice_text: text, draft_text: hit ? text.replace("نُه یک یک", "نهصد و یازده") : null,
    text: hit ? text.replace("نُه یک یک", "نهصد و یازده") : text, edited: hit, needs_sending: hit,
    words: words(hit ? text.replace("نُه یک یک", "نهصد و یازده") : text).map((w) => (hit && ["نهصد", "و", "یازده"].includes(w.surface) ? { ...w, corrected: true } : w)),
    takes: [{ ...take(1, true, "original"), audio_url: `/api/x${n}` }],
  });
  const plain = [mk(1, "او آمد."), mk(2, "تماس نُه یک یک بود."), mk(3, "بعد نُه یک یک رفت.")];
  const done = [plain[0], mk(2, "تماس نُه یک یک بود.", true), mk(3, "بعد نُه یک یک رفت.", true)];
  const overviewOf = (blocks: unknown[]) => ({ exists: true, version_id: 7, language: "fa", status: "open", assembled_current: false, assembled_at: null, approved: false,
                                              corrected_blocks: blocks.filter((b) => (b as { edited: boolean }).edited).length, to_send: blocks.filter((b) => (b as { needs_sending: boolean }).needs_sending).length,
                                              assembled_audio_url: null, blocks });
  const matches = [2, 3].map((n) => ({ block_id: `FA_B0${n}_g01_01`, position: n - 1, count: 1, before: "تماس نُه یک یک بود", after: "تماس نهصد و یازده بود" }));
  const calls: { find: string; replace: string; apply: boolean }[] = [];
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
  await page.route("**/api/documentary/pronunciation/entries?**", (r: Route) => r.fulfill(ok({ counts: {}, entries: [] })));
  await page.route("**/api/documentary/versions/7/block-review", (r: Route) => r.fulfill(ok(overviewOf(plain))));
  await page.route("**/api/documentary/versions/7/block-review/replace", async (r: Route) => {
    const body = r.request().postDataJSON() as { find: string; replace: string; apply: boolean };
    calls.push(body);
    await r.fulfill(ok(body.apply ? { ...overviewOf(done), applied: true, blocks_found: 2, places_found: 2, matches } : { applied: false, blocks_found: 2, places_found: 2, matches }));
  });
  await page.goto("/documentary/1?tab=Pronunciation");
  await page.getByRole("button", { name: "Voice review", exact: true }).click();
  await page.getByText("Replace in all blocks").click();
  await expect(page.getByRole("button", { name: "Find the places" })).toBeDisabled();
  await page.getByLabel("Words to find").fill("نُه یک یک");
  await page.getByLabel("Words to put in their place").fill("نهصد و یازده");
  await page.getByRole("button", { name: "Find the places" }).click();
  await expect(page.getByLabel("Places found").getByRole("listitem")).toHaveCount(2);
  await expect(page.getByTestId("block-review")).toContainText("0 corrected · 0 to send");           // a preview changes nothing
  await page.getByRole("button", { name: "Replace in 2 blocks (2 places)" }).click();
  await expect(page.getByTestId("block-review")).toContainText("2 corrected · 2 to send");
  await expect(page.getByTestId("block-FA_B02_g01_01")).toHaveAttribute("data-edited", "true");
  await expect(page.getByTestId("block-FA_B03_g01_01").locator("[data-corrected=true]")).toHaveCount(3);
  await expect(page.getByTestId("block-FA_B01_g01_01")).toHaveAttribute("data-edited", "false");
  expect(calls.map((c) => c.apply)).toEqual([false, true]);
});
