import type { Route } from "@playwright/test";
import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const settings = {
  languages: ["en", "fa"], film_minutes: { min: 45, max: 120 }, pilot_seconds: 120, voices: {}, channels: { en: { name: "ClueVera" } }, avatar_generation: { enabled: true },
  voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: [], publish: [] }, dynamic_eq: {},
};
const overview = { finals: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null, audio_plan: null, languages: { fa: { version_id: 7 } }, visual_plan: null, jobs: [], visual_counts: {} };
const costs = { case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] };
const ok = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const words = (text: string, corrected: Record<number, string> = {}) =>
  text.split(" ").map((surface, n) => ({ n, surface, corrected: n in corrected, was: corrected[n] ?? "", finglish: n in corrected ? "dore" : "", pending: n in corrected }));
const take = (take_no: number, active: boolean, source: "original" | "correction") => ({
  voice: { speed: 1.2, stability: 0.2, post_speed: 1.1 }, take_no, source, active, duration_s: 6.1, cost_eur: source === "correction" ? 0.0031 : 0, text: "", aliases: [], created_at: "2026-11-06T10:00:00Z",
  audio_url: `/api/documentary/versions/7/host-voice-review/blocks/host11/takes/${take_no}/audio`,
});

test("the host voice review is the voice review of the host's scenes: no approve and no assemble, corrections are voiced and ticked on the host routes", async ({ page }) => {
  let block = { block_id: "host11", position: 0, canonical_text: "ببین دورِ تخت", voice_text: "ببین دورِ تخت", draft_text: null as string | null, text: "ببین دورِ تخت", edited: false,
                needs_sending: false, words: words("ببین دورِ تخت"), takes: [take(1, true, "original")] };
  const state = () => ({ exists: true, kind: "host", version_id: 7, language: "fa", status: "open", assembled_current: false, assembled_at: null, approved: false,
                         corrected_blocks: block.edited ? 1 : 0, to_send: block.needs_sending ? 1 : 0, assembled_audio_url: null, blocks: [block] });
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
  await page.route("**/api/documentary/versions/7/block-review", (r: Route) => r.fulfill(ok({ exists: false, version_id: 7, can_open: false })));
  await page.route("**/api/documentary/versions/7/host-voice-review", (r: Route) => r.fulfill(ok(state())));
  await page.route("**/api/documentary/versions/7/host-voice-review/blocks/host11", async (r: Route) => {
    block = { ...block, edited: true, needs_sending: true, draft_text: "ببین دُورِ تخت", text: "ببین دُورِ تخت", words: words("ببین دُورِ تخت", { 1: "دورِ" }) };
    await r.fulfill(ok(block));
  });
  await page.route("**/api/documentary/versions/7/host-voice-review/blocks/host11/send", async (r: Route) => {
    block = { ...block, needs_sending: false, draft_text: null, voice_text: block.text, words: block.words.map((w) => ({ ...w, pending: false })), takes: [take(1, false, "original"), take(2, true, "correction")] };
    await r.fulfill(ok(block));
  });
  await page.route("**/api/documentary/versions/7/host-voice-review/blocks/host11/takes/*/activate", async (r: Route) => {
    const no = Number(r.request().url().split("/takes/")[1].split("/")[0]);
    block = { ...block, takes: block.takes.map((t) => ({ ...t, active: t.take_no === no })) };
    await r.fulfill(ok(block));
  });

  await page.goto("/documentary/1?tab=Voice%20Review");
  await page.getByRole("button", { name: "Host voice review" }).click();
  const review = page.getByTestId("block-review");
  await expect(review).toContainText("1 blocks · 0 corrected · 0 to send");
  await expect(page.getByTestId("review-state")).toHaveText("host voice");
  await expect(page.getByRole("button", { name: /Approve this narration/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Preview the assembled narration/ })).toHaveCount(0);
  await expect(review).toContainText("Approving the film's voice is done in Voice review only");

  const card = page.getByTestId("block-host11");
  await card.getByRole("button", { name: "Correct" }).click();
  await page.getByLabel("Persian word 1").fill("دُورِ");
  await page.getByLabel("Finglish of word 1").fill("dore");
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(card).toHaveAttribute("data-edited", "true");
  await card.getByRole("button", { name: "Send to ElevenLabs" }).click();
  await expect(page.getByTestId("voice-host11-2")).toContainText("speed 1.2 · stability 0.2 · ×1.1");
  await page.getByLabel("Use take 1 of host11").click();
  await expect(card.locator("li[data-active=true]")).toContainText("take 1");

  await page.getByRole("button", { name: "Voice review", exact: true }).click();
  await expect(page.getByText("No voice review yet")).toBeVisible();
});
